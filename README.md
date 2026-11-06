# estate-ci

The estate's standard CI/CD path: GitHub Actions on self-hosted ARC runners in
the `vallery` k3s cluster, in-cluster image builds pushed to
`registry.vallery.net` by digest, and digest pins delivered by Argo CD. It has
no dependency on the jvallery/agents fleet code (native-ci controller, repo
cells, brokers, repo-auth, the fleet registry or its ARC renderer).

Migrating a repo: [`docs/MIGRATION-RUNBOOK.md`](docs/MIGRATION-RUNBOOK.md).
Tracking: jvallery/agents#6092 (FLEET-DECOM epic), jvallery/agents#6094 (this foundation).

## What is here

| Path | What |
|---|---|
| `.github/workflows/checks.yml` | Reusable: run one check command (optionally inside the repo's CI image, optionally with Postgres). |
| `.github/workflows/image-build.yml` | Reusable: Kaniko build in-cluster; on push, crane push to zot and output the digest. |
| `.github/workflows/pin-bump.yml` | Reusable: open a PR that rewrites digest pins in GitOps manifests, then dispatch the repo's checks on it. |
| `actions/sidecar-run`, `actions/setup-crane` | Composite actions for custom jobs on estate-ci runners. |
| `runners.yaml` | The runner registry: which repos get which scale sets. |
| `scripts/render_runners.py` | Renders `runners.yaml` into `deploy/runners/apps/` (one Argo Application per scale set). |
| `deploy/foundation/` | Namespace `ci-runners`, ResourceQuota/LimitRange, NetworkPolicies, the runner scripts ConfigMap. |
| `docs/examples/` | Caller workflows to copy. |

## Design

**Runners.** The existing ARC controller (`arc-systems/arc-gha-rs-controller`,
gha-runner-scale-set 0.14.2, cluster-wide) serves scale sets in namespace
`ci-runners`. Each set is an Argo Application of the upstream chart with
values rendered from `runners.yaml`; sets scale from 0. `jvallery` is a user
account, so its sets are per repository; an organization (VerdifyConsultancy)
can use one org-level set for all its repos. The scale-set name is the
`runs-on` label: `<repo>-ci`, `<repo>-pg`, `<repo>-build`.

| Profile | Pod | Use |
|---|---|---|
| `ci` | runner (1 CPU / 1Gi → 4 CPU / 4Gi); optional `job` sidecar running the repo's own CI image | lint, unit tests, render/regen checks |
| `pg` | runner + Postgres sidecar on `127.0.0.1:5432` (emptyDir, fsync off; CNPG image with pgvector) [+ job] | database-backed suites; no PVC or CNPG cluster per run |
| `build` | runner + Kaniko sidecar | image builds |

There is no Docker daemon, container socket, privileged container, hostPath,
Kubernetes API token or RBAC in a runner Pod. Sidecars are driven through a
Pod-local file exchange (`deploy/foundation/files/sidecar-agent.sh`): the
runner writes a script into a shared emptyDir, the sidecar runs it and streams
the log and exit code back. That gives "run this step in my CI image" and
"build this image" without Docker-in-Docker or Kubernetes container mode.

**Admission.** `ci-runners` enforces Pod Security `baseline` (warn/audit
`restricted`). Only the Kaniko sidecar runs as root, with the default
capability set and RuntimeDefault seccomp, which baseline admits (native CI
runs the same Kaniko image in baseline `agent-fleet-ci`). Everything else runs
as non-root with all capabilities dropped. A job-started hook
(`job-started-guard.sh`, from a ConfigMap, so a PR cannot change it) refuses
jobs from another repository, fork pull requests, `pull_request_target` and
`workflow_run` events, and non-build events on build runners.

**Network.** Runner Pods accept no ingress. Egress: DNS, public 80/443 (private
ranges excluded), the split-horizon apps ingress `192.168.7.10:443` (how
`registry.vallery.net` resolves in-cluster), and the zot origin on 5000.

**Capacity.** One namespace ResourceQuota is the hard envelope for all sets
(`deploy/foundation/quota.yaml`). It starts at pilot size and grows only by
what each cutover removes from the repo's native-ci budget.

**Builds and the registry.** Kaniko builds to a tarball with `--no-push`,
holding only the cluster pull credential. On a push to the default branch the
build job enters the caller's GitHub environment `registry` (deployment
branches: default branch only), whose secrets `ESTATE_CI_REGISTRY_USERNAME`
and `ESTATE_CI_REGISTRY_PASSWORD` are that repo's own zot principal (write
access to only its image paths, in storage-infra's zot ACL). The runner
container pushes the tarball with crane to the zot origin as
`<image>:gha-<sha12>`, reads the digest back, and outputs
`registry.vallery.net/<image>@sha256:…`. PR jobs never get the write
credential, and Dockerfile `RUN` steps never see it.

**Pins.** `pin-bump.yml` rewrites digests (kustomize `images:` entries or
inline `<image>@sha256:` references), pushes a branch and opens a PR with
`GITHUB_TOKEN`. Because GitHub does not start workflows for `GITHUB_TOKEN`
events, it then `workflow_dispatch`es the repo's checks workflow on the pin
branch, so the PR head gets the same check runs required checks expect.
Merging the PR is the deploy; Argo CD syncs it. No extra GitHub App is needed.

**Registration.** Runners register with the existing GitHub App
`vallery-agent-fleet-ci` (installations on `jvallery` and
`VerdifyConsultancy`, repository selection "all", permissions
administration:write and organization self-hosted runners). Its credentials are
copied into `ci-runners/arc-github-app-<owner>` through central KSOPS. New
repositories are covered automatically. Do not delete this App while any
scale set uses it (see jvallery/agents#6092 J-5).

**Delivery.** Argo CD Applications `estate-ci-foundation`, `estate-ci-runners`
(app-of-apps over `deploy/runners/apps`) and `estate-ci-secrets` (central
KSOPS) in AppProject `estate-ci` are declared once in jvallery/agents.
After that, onboarding a repo touches only this repository and the repo itself.

**Concurrency.** Callers set
`concurrency: {group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }}}`,
so a new push to a PR cancels its superseded run, and runs on `main` queue
instead of cancelling each other.

## Using it

Pin every `uses: jvallery/estate-ci/...@<sha>` to a full commit SHA. See
`docs/examples/ci.yml` and `docs/examples/build.yml`.
