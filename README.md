# estate-ci

The estate's standard CI/CD path: GitHub Actions on self-hosted ARC runners in
the `vallery` k3s cluster, in-cluster image builds pushed to
`registry.vallery.net` by digest, and digest pins delivered by Argo CD. It has
no dependency on the jvallery/agents fleet code (native-ci controller, repo
cells, brokers, repo-auth, the fleet registry or its ARC renderer).

Migrating a repo: [`docs/MIGRATION-RUNBOOK.md`](docs/MIGRATION-RUNBOOK.md).
Tracking: jvallery/agents#6092 (FLEET-DECOM epic), jvallery/agents#6094 (this foundation).
Decisions and exceptions: [`docs/adr/`](docs/adr/README.md); the one Actions-secret
exception (D-52, `PIN_BOT_PRIVATE_KEY`) is E-2 in ADR-0011.

## What is here

| Path | What |
|---|---|
| `deploy/foundation/bin/` | **All CI logic.** Mounted at `/opt/estate-ci` (`$ESTATE_CI`) in every runner Pod: `image-build.sh`, `pin-bump.sh` + `forge-api.sh` + `app-token.py`, `sidecar-run.sh`, `wait-postgres.sh`, `check-runner-labels.sh`, and the job guard. |
| `.github/workflows/checks.yml` | Thin reusable wrapper: checkout + one repo command (optionally inside the repo's CI image, optionally with Postgres). |
| `.github/workflows/image-build.yml` | Thin reusable wrapper over `image-build.sh`. |
| `.github/workflows/build-plan.yml` | Thin reusable wrapper over `build-plan.py`, the one image build planner: the caller's `ci/images.tsv` plus changed paths or dispatched names in, the `image-build.yml` matrix JSON out (estate-ci#156). |
| `.github/workflows/kustomize-validate.yml` | Thin reusable wrapper over `kustomize-validate.sh`: kustomize build + kubeconform `-strict` of the given roots (estate-ci#156). |
| `.github/workflows/pin-bump.yml` | Thin reusable wrapper over `pin-bump.sh`. |
| `.github/workflows/lint.yml` | Reusable R1-R3 workflow lint (`deploy/foundation/bin/lint-workflows.py`, stdlib Python; fixtures in `tests/lint/`; recorded exceptions in the caller's `.estate-ci/workflow-lint-allow.txt`, one `<path glob> <rule> <expiry> <issue URL>` row per dated exception row in the estate GitHub security baseline, tests in `tests/lint_allow/`), plus R4 image lint (`deploy/foundation/bin/lint-images.py`: `imagePullPolicy: Always` on an image outside registry.vallery.net fails, and so does an image or Helm `repository`/`tag` pair with no `@sha256` digest (`R4-tag-only`, a warning until `TAG_ONLY_ENFORCE_FROM` in `lint-workflows.py`, estate-ci#104); allowlist `.estate-ci/image-lint-allow.txt` rows carry an expiry and an issue; fixtures in `tests/lint-images/`), plus the STD-OPS-02 component version register check (`deploy/foundation/bin/lint-version-register.py`, schema `deploy/foundation/schemas/version-register.schema.json`, estate-ci#121): opt-in, it runs only when the caller has a `version-register.json` at its root; fixtures in `tests/version_register/`. |
| `scripts/fork-policy.sh` | Operator check/apply of the fork-PR approval policy for onboarded repos. |
| `.github/workflows/mirror.yml`, `mirrors.txt`, `.github/workflows/mirror-images.yml` | Reusable digest-preserving image mirror (`crane copy`, `deploy/foundation/bin/mirror.sh`); estate-ci's own mirror list, validated on PRs and copied when `mirror-images` is dispatched on `main`. |
| `images/estate-ci-toolbox/`, `.github/workflows/build.yml` | The estate-ci-toolbox job image (tools and versions in its README), built in-cluster by Kaniko: no-push on PRs, pushed when `build` is dispatched on `main`. |
| `runners.yaml` | The runner registry: which repos get which scale sets. |
| `scripts/render_runners.py` | Renders `runners.yaml` into `deploy/runners/apps/` (one Argo Application per scale set). |
| `deploy/foundation/` | Namespace `ci-runners`, ResourceQuota/LimitRange, NetworkPolicies, the runner scripts ConfigMap. |
| `docs/examples/` | Caller workflows to copy. |

## Design

**Runners.** The existing ARC controller (`arc-systems/arc-gha-rs-controller`,
gha-runner-scale-set 0.14.2, cluster-wide) serves scale sets in namespace
`ci-runners`. Each set is an Argo Application of the upstream chart with
values rendered from `runners.yaml`; sets scale from 0. `jvallery` is a user
account, so its sets are per repository. Only jvallery repositories get sets
(D19); VerdifyConsultancy repositories move to jvallery before they use CI. The scale-set name is the
`runs-on` label: `<repo>-ci`, `<repo>-pg`, `<repo>-build`.

| Profile | Pod | Use |
|---|---|---|
| `ci` | runner (1 CPU / 1Gi → 4 CPU / 4Gi); optional `job` sidecar running the repo's own CI image | lint, unit tests, render/regen checks |
| `pg` | runner + Postgres sidecar on `127.0.0.1:5432` (emptyDir, fsync off; CNPG image with pgvector) [+ job] | database-backed suites; no PVC or CNPG cluster per run |
| `build` | runner + Kaniko sidecar + the repo's zot push secret | image build + push; default-branch push/dispatch only |
| `buildcheck` | runner + Kaniko sidecar, no credential | PR image builds (no push) |

There is no Docker daemon, container socket, privileged container, hostPath,
Kubernetes API token or RBAC in a runner Pod. Sidecars are driven through a
Pod-local file exchange (`deploy/foundation/bin/sidecar-agent.sh`, client `sidecar-run.sh`): the
runner writes a script into a shared emptyDir, the sidecar runs it and streams
the log and exit code back. That gives "run this step in my CI image" and
"build this image" without Docker-in-Docker or Kubernetes container mode.

**Admission.** `ci-runners` enforces Pod Security `baseline` (warn/audit
`restricted`). Only the Kaniko sidecar runs as root, with the default
capability set and RuntimeDefault seccomp, which baseline admits. Everything else runs
as non-root with all capabilities dropped. A job-started hook
(`job-started-guard.sh`, from a ConfigMap, so a PR cannot change it) refuses
jobs from another repository, fork pull requests, `pull_request_target` and
`workflow_run` events, and anything but a default-branch push or dispatch on a
`build` runner.

**Network.** Runner Pods accept no ingress. Egress: DNS, public 80/443 (private
ranges excluded), the split-horizon apps ingress `192.168.7.10:443` (how
`registry.vallery.net` resolves in-cluster), and the zot origin on 5000.

**Capacity.** The gate is node allocatable CPU plus the per-node
`agentfleet.vallery.net/ci-runner-slot` cap (ADR-0010; live 2026-11-09:
node4-7 20, node8 4). Runner Pods request measured use per class. The quotas
in `deploy/foundation/quota.yaml` are sized to every set at `maxRunners`:
`estate-ci-runner-pods` is the compute envelope for runner Pods,
`estate-ci-runners` holds the namespace object and ephemeral caps, and
`estate-ci-ops` budgets the hot guard. A `maxRunners` raise raises the
envelope in the same PR, only with live headroom on the workers. Native CI is
retired (jvallery/agents#6254), so there is no native budget to move.

**Builds and the registry.** `image-build.sh` drives Kaniko to build a
tarball with `--no-push`; Kaniko holds only the cluster pull credential. With
`--push` (only possible on the `<repo>-build` set) the runner container pushes
the tarball with crane to the zot origin as `<image>:gha-<sha12>`, reads the
digest back and outputs `registry.vallery.net/<image>@sha256:…`. The push
credential is that repo's own zot principal (write to only its image paths, in
storage-infra's zot ACL), delivered by central KSOPS as
`ci-runners/zot-push-<repo>` and mounted only into that repo's `build` runner
container. PR jobs (other sets) never have it and Dockerfile `RUN` steps never
see it. No GitHub Environments, OIDC, Actions cache or artifact service.

**Pins.** `pin-bump.sh` rewrites digests (kustomize `images:` entries or
inline `<image>@sha256:` references), pushes a branch and opens a PR. The PR is
opened with an installation token of the GitHub App `vallery-pin-bot` (D-52;
contents + pull_requests write, scoped per run to the calling repo), minted by
`app-token.py` (stdlib, no third-party action) from the caller-passed secret `PIN_BOT_PRIVATE_KEY` and repo
variable `PIN_BOT_CLIENT_ID`, so `pull_request` workflows run and required
checks attach. Without them it falls back to `GITHUB_TOKEN`, which starts no
workflows: the checks workflow is then `workflow_dispatch`ed on the pin branch,
but the PR stays BLOCKED on required checks. Branch and commit are plain git;
the PR, dispatch and auto-merge calls are in `forge-api.sh` (token from
`FORGE_TOKEN`), the one file to swap for another forge (`FORGE=forgejo` is
implemented against the Gitea/Forgejo API). Merging the PR is the deploy;
Argo CD syncs it.

**Registration.** Runners register with the dedicated least-privilege GitHub App
`vallery-ci-runners` (J-10; App ID 5636090, installation 179108157 on `jvallery`,
selected repositories, Administration RW + Metadata RO, no webhook). Its
credential is `ci-runners/arc-github-app-jvallery` in central KSOPS. A repository
joins by being added to the installation's selected repositories. ARC is jvallery
only (D19): there is no VerdifyConsultancy installation or org-level set.

**Delivery.** Argo CD Applications `estate-ci-foundation` (AppProject
`estate-ci`), `estate-ci-runners` (app-of-apps over `deploy/runners/apps`,
AppProject `estate-ci-apps`; the per-set `estate-ci-runner-*` Applications are
in `estate-ci`) and `estate-ci-secrets` (central KSOPS, AppProject
`estate-ci-secrets`) are declared once in jvallery/agents (planned to move to
jvallery/estate-platform).
After that, onboarding a repo touches only this repository and the repo itself.

**Concurrency.** Callers set
`concurrency: {group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }}}`,
so a new push to a PR cancels its superseded run, and runs on `main` queue
instead of cancelling each other.

## Estate CI rules (COORD CI DESIGN RULES, 2026-11-06)

- **R1** Every job runs on an ARC set; no GitHub-hosted runner labels anywhere
  (this repo's own `self-test` runs on `estate-ci-ci`). `check-runner-labels.sh`
  enforces it: every repo's CI should call `"$ESTATE_CI/check-runner-labels.sh" .`.
- **R2** Workflow YAML stays thin: `actions/checkout` plus these reusable
  workflows only; logic lives in repo scripts and `/opt/estate-ci`.
- **R3** Each workflow file states its Forgejo/Gitea Actions notes; the runbook
  has the portability checklist.

## Using it

Pin every `uses: jvallery/estate-ci/...@<sha>` to a full commit SHA. See
`docs/examples/ci.yml` and `docs/examples/build.yml`. The reusable workflows
are conveniences: a job that is `actions/checkout` plus
`run: "$ESTATE_CI/image-build.sh" ...` is equivalent and forge-neutral.

## Image mirrors

An image whose Dockerfile is only `FROM <upstream>@<digest>` is a mirror, not a
build: list it in the repo's `mirrors.txt` and call `mirror.yml` (no Kaniko).

```
# mirrors.txt: <upstream-ref>@sha256:<digest>  <dest-repository>
ghcr.io/kube-vip/kube-vip:v1.0.4@sha256:<digest>  jvallery/network-infra/mirror/ghcr.io/kube-vip/kube-vip
```

PRs check the list (format, destination prefix, the upstream digest resolves)
on `<repo>-ci` without a credential. On the default branch, `apply: true`
copies each missing digest to the zot origin on `<repo>-build` with the repo's
own writer and reads the digest back; present digests are skipped. Each copy
is tagged `pinned-<digest12>` so zot retention keeps it.

`crane copy` is byte-for-byte, so **the mirror's digest is the upstream
digest**: an image index stays an index (list the `linux/amd64` child digest to
copy one platform, as the large vLLM/GPUStack mirrors should).

**One-time pin change per mirror.** A Kaniko-built mirror
(`registry.vallery.net/jvallery/mirror-<name>-<hash>@sha256:<kaniko digest>`)
has a digest of its own. Moving it to `crane copy` changes the pin once:

1. Add the line to `mirrors.txt` (upstream ref and digest from the old Dockerfile's `FROM`).
2. Merge; dispatch or push runs `apply`; the log prints
   `registry.vallery.net/<dest>@<upstream digest>`.
3. In one PR, repin every consumer to that reference and delete the old
   mirror Dockerfile and its build entry. The old repository stays in zot
   until retention or the storage-infra custody plan removes it.

estate-ci's own `mirrors.txt` carries the ARC runner and the Kaniko executor
(the pins in `runners.yaml` keep their digests and change only their
repository, to `registry.vallery.net/jvallery/estate-ci/mirror/...`) and the
toolbox bases. Its writer is `zot-push-estate-ci` (write on
`jvallery/estate-ci/**` only).
