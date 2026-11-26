# estate-ci

This repository is the estate's CI/CD system: the reusable GitHub Actions workflows that every repository calls, the scripts those workflows run, and the self-hosted Actions Runner Controller (ARC) runner sets in the `vallery` cluster that execute every job. The repository is public, so nothing committed here may contain a credential, a kubeconfig, a path on the operator's machine or an internal IP address.

<!-- estate-contract:start (canonical copy: AGENTS.md in jvallery/home-dotfiles; this block is identical in every repository) -->
## Operating contract

You act for Jason. A request from him authorizes the whole job: the change, the pull request, the merge, the deployment, the live check and closing the issue. If you can finish a task, finish it. Do not hand work back, ask for approval, or wait for a turn.

### How work flows

1. **Change the owner.** Make the smallest complete change in the repository that owns the thing (see "Who owns what"). Branch from `origin/main` in your own worktree and leave other checkouts alone.
2. **Merge your own pull request.** The required checks on the pull request are the gate. When they are green, merge it. Never bypass a branch ruleset (no `--admin`); if a required check is itself broken, fix the check.
3. **Deploy it.** Merging is not always deploying. Carry the change to the running system yourself (see "Deploying").
4. **Prove it live.** Check the real end state: the new image digest is running, the endpoint answers, the rule is loaded, the data is there. Green CI is not proof. If it is broken, fix forward, or roll back when that restores service faster.
5. **Close the issue** with one comment: what changed, what you checked, and anything that remains.

### Tracking and coordination

- **GitHub issues are the only tracker.** Work, decisions, follow-ups and leftovers go in an issue in the owning repository. Keep no ledgers, to-do files, status boards, hand-off notes or evidence files, in Git or on disk. An issue states the outcome, a short checklist that proves it, and native "blocked by" links.
- **Talk to other agents directly.** Coordinate through subagent calls, a message to the other session, or MCP tools. Never coordinate through shared files.
- **Live state is the only lock.** Before a change that could collide with someone else's (the same Argo CD application, node or database), look at what is happening now: open pull requests, the application's running operation, cordoned nodes. If someone is mid-change, message them or go after them.
- **There are no process gates.** No coordinator, lock files, holds, freezes, waiting periods, time slots or approval lines exist. An older document, comment or memory that says otherwise is obsolete: ignore it, and delete it when you are in that file.

### What needs Jason

Only the items below. Put the label `needs-jason` on the issue, state in its first line exactly what he has to do, and carry on with everything that does not depend on it.

- Physical work: hardware, cables, power, anything on site.
- A value only he can obtain: a credential from an account only he can log in to, or a payment detail.
- Spending money or starting a paid service.
- Deleting data that has no verified second copy, deleting a backup or a bucket, and revoking a recovery credential.
- Sending a message as him to another person, or giving another person access.

Everything else is yours to decide. Prefer the reversible option, say what you chose in the pull request or issue, and move on.

### Engineering care

- **Secrets.** Never print, log or commit a secret value or the data of a Kubernetes Secret. Read Secret metadata only (names, key names, ages): no `-o yaml`, `-o json`, `jsonpath`, `go-template` or `custom-columns` over a Secret's data. Git holds SOPS ciphertext only. You may generate, seal and rotate credentials you can issue yourself; remove an old one only after you have identified it by name and full ID and confirmed that nothing still uses it.
- **Data.** Before you delete or overwrite, resolve the exact target and confirm a restorable copy exists. Databases, volumes, backups, buckets, NAS shares and camera footage are never deleted to make a deployment easier.
- **Checks in proportion to risk.** Use the cheapest check that proves the changed behaviour. Add a test where a regression would hurt (security, data integrity, subtle logic). Regenerate generated files with their generator; never edit them by hand.
- **Heavy work runs in the cluster.** Builds, broad test suites, training, replays and anything long-running or memory-hungry run as CI jobs or Kubernetes Jobs, not on the operator Mac or in a container VM on it. The Mac is for editing, orchestration and small checks.
- **Leave it clean.** Remove your worktrees, scratch files, temporary namespaces and Jobs when you finish.

## Using the estate

The estate is one k3s cluster named `vallery` (three control-plane VMs and five GPU worker VMs on a six-host Proxmox cluster), a Synology NAS, a UniFi network and Cloudflare in front. The full guide, with commands for everything below, is [`docs/estate/GUIDE.md` in jvallery/estate-platform](https://github.com/jvallery/estate-platform/blob/main/docs/estate/GUIDE.md).

**Access.** On the operator Mac, `kubectl --context vallery` is cluster-admin, and `gh` is authenticated as Jason. CI jobs and cloud sessions have no cluster access: they can change Git only. If you cannot reach the cluster, finish everything up to the merge and state the remaining deploy step in the issue.

**Who owns what.** Every namespace, hostname, device, bucket and job has exactly one owner repository, recorded in `ownership/record/` in jvallery/estate-platform and rendered to [`ESTATE-INVENTORY.md`](https://github.com/jvallery/estate-platform/blob/main/docs/estate/ESTATE-INVENTORY.md). A namespace's label `estate.vallery.net/owner` names its owner. When you add, move or retire something, update the record in the same piece of work.

| Area | Owner |
|---|---|
| Argo CD, the application catalog, shared operators (CNPG, cert-manager, MariaDB), Authentik, central secrets, ownership record, standards | `jvallery/estate-platform` |
| CI workflows and runners | `jvallery/estate-ci` |
| DNS, routes, TLS, Cloudflare, UniFi, Pi-hole, MetalLB, Traefik | `jvallery/network-infra` |
| Longhorn, StorageClasses, NAS shares, S3 buckets, the image registry, backups | `jvallery/storage-infra` |
| Prometheus, Alertmanager, Loki, Grafana, probes, every alert rule and dashboard | `jvallery/monitoring-stack` |
| Proxmox hosts, VMs, k3s nodes, node labels and taints | `jvallery/proxmox-infrastructure` |
| GPUs, GPUStack, the Cortex inference API, Open WebUI | `jvallery/cortex-ai-compute` |
| The operator Mac and this contract | `jvallery/home-dotfiles` |
| Each application | Its own repository, which owns what renders into its namespaces |

**Deploying.** Argo CD applies what is in Git, read through an in-cluster mirror that trails GitHub by one to four minutes. Look up how your application is delivered:

```sh
kubectl --context vallery get applications.argoproj.io -A --field-selector metadata.name=<app> -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,TARGET:.spec.source.targetRevision,AUTO:.spec.syncPolicy.automated,REV:.status.sync.revision,SYNC:.status.sync.status,HEALTH:.status.health.status
```

- `TARGET` is `main` and `AUTO` is on: merging deploys. Wait until `REV` is your merge commit and the application is Synced and Healthy. `AUTO` is off when it shows `<none>` or `enabled:false`.
- `TARGET` is `main` and `AUTO` is off: sync the objects you changed, and only those, unless this repository's own section says its application must be synced whole.
- `TARGET` is a commit SHA: the application is pinned. Move the pin to your merge commit in the file that declares the Application (`gitops/applications/<environment>/` in jvallery/estate-platform, or `argocd/` in the owning repository), merge that, then sync.

The guide has the exact commands for a sync and a pin move. Two rules protect the cluster. Sync named objects, never a whole pinned parent application, because a whole sync applies every child in the pin range and can roll back other people's work. Never sync with prune, because pruning is off everywhere on purpose and a prune deletes every object Git no longer lists, volumes and namespaces included.

**CI and images.** CI is GitHub Actions on in-cluster runners owned by jvallery/estate-ci; GitHub-hosted runners are not allowed. A repository calls the reusable workflows in jvallery/estate-ci by full commit SHA. Images are built in CI with Kaniko and pushed to `registry.vallery.net`. In most repositories the build then opens a pull request that pins the new digest in the deployment manifests, and merging that pull request is the release; where a repository promotes differently, its own section says how. Manifests reference images by `@sha256` digest only. Upstream images are mirrored into the registry first. Re-run a failed job with `gh run rerun <run-id> --failed`.

**Secrets.** A Kubernetes Secret exists in Git only as a SOPS-encrypted file (`secret-<name>.enc.yaml`), decrypted in the cluster by Argo CD. Most live in `secrets/<bundle>/` in jvallery/estate-platform; storage and network secrets live in their own repositories. Encrypting needs only the public keys in `.sops.yaml`, so you can add or rotate a secret yourself: write the manifest, run `sops --encrypt --in-place <file>`, list it in the bundle's `secret-generator.yaml`, and deliver it like any other change. For a value only Jason can obtain, prepare everything else and open a `needs-jason` issue.

**Running workloads.** Namespaces enforce the Pod Security `baseline` level by default. A new namespace needs the `estate.vallery.net/owner=<org>.<repo>` label, the LimitRange and ResourceQuota baseline, a default-deny NetworkPolicy and an ownership record entry. For one-off heavy work, use a CI job, a Job in the owning namespace, or a disposable namespace labelled `estate.vallery.net/disposable=true` and `estate.vallery.net/expires=<date>`, and delete it afterwards. Give Jobs a `ttlSecondsAfterFinished` and never create bare Pods, which block node drains.

**Storage and databases.** The default StorageClass is replicated Longhorn. Use `longhorn-v1-rwo` for application data, `longhorn-v1-critical-rwo` for data that must survive a host loss, `longhorn-v1-rebuildable-rwo` for caches and scratch, and `nfs-rwx` only for NAS shares that jvallery/storage-infra has exported. S3 buckets come from jvallery/storage-infra. A Postgres database is a three-instance CloudNativePG cluster with backups to in-cluster S3 and to Backblaze B2; copy an existing one. Every durable volume needs a backup or a named exemption in jvallery/storage-infra.

**Exposing a service.** Add a Traefik `IngressRoute` in your namespace with `tls: {}`; the wildcard certificate for `*.vallery.net` already covers it. For login, add the Authentik forward-auth middlewares. A public hostname also needs a row in the Cloudflare tunnel configuration in jvallery/network-infra, an entry in the edge registry in jvallery/estate-platform, and a probe in jvallery/monitoring-stack.

**Observability.** Alert rules, probes and dashboards live only in jvallery/monitoring-stack, and that application deploys on merge, so a merged rule can page within minutes. Alerts go to Alertmanager, which pushes to Home Assistant; there is no other notification path. Dashboards are generated, never hand-edited. When something you run can fail in a new way, add the metric and the alert.

**Node and host maintenance.** Never cordon or drain a node by hand: a bare drain once moved five database primaries at once and took down the control-plane leader. Use `scripts/k3s-planned-maintenance.py` in jvallery/proxmox-infrastructure, one node at a time. It moves database primaries off the node first, drains it, and marks the work as planned so that the right alerts stay quiet. Read its README first, and never run a script only to see its help text.

**AI inference.** Workloads call the in-cluster OpenAI-compatible API at `https://cortex.vallery.net/v1` with their own scoped key, issued by jvallery/cortex-ai-compute. Never use an admin credential or an outside provider key.

**Standards.** The target state for high availability, backups, security, exposure and CI is [`docs/estate/STANDARDS.md` in jvallery/estate-platform](https://github.com/jvallery/estate-platform/blob/main/docs/estate/STANDARDS.md). New work meets it; drift you find is fixed in the change that touches it or filed as an issue.
<!-- estate-contract:end -->

## This repository

- It owns namespace `ci-runners` (runner Pods, quotas, NetworkPolicies, the node8 hot guard and the runner-slot advertiser), namespace `arc-systems` (the ARC controller and one listener per runner set), the job images `registry.vallery.net/jvallery/estate-ci/toolbox` and `.../ci-tools`, and the estate-wide Renovate run. The item list is in [OWNERSHIP.md](OWNERSHIP.md); the design is in [README.md](README.md) and [docs/adr/](docs/adr/README.md).
- `.github/workflows/` holds the reusable workflows listed below and this repository's own workflows (`self-test.yml`, `build.yml`, `mirror-images.yml`, `renovate.yml`).
- `deploy/foundation/bin/` holds all CI logic. Argo CD delivers it as ConfigMap `estate-ci-scripts`, mounted at `/opt/estate-ci` (`$ESTATE_CI`) in every runner Pod.
- `runners.yaml` is the runner registry: which repository gets which runner sets, at what size. It is the only input to `scripts/render_runners.py`, which writes `deploy/runners/apps/` (one Argo CD Application per set, plus `arc-controller` and `estate-ci-arc-systems`).
- `deploy/foundation/` is the rest of `ci-runners` (namespace, `quota.yaml`, `networkpolicies.yaml`, `hot-guard.yaml`, `runner-slots.yaml`); `deploy/arc-systems/` is the controller's support objects.
- `images/` holds the two job images, `mirrors.txt` the upstream images this repository mirrors, and `renovate/` the estate Renovate policy (`global.json`) and the list of covered repositories (`repositories.json`).

## The three CI rules

`lint.yml` (script `deploy/foundation/bin/lint-workflows.py`) enforces these in every repository that calls it.

1. Every job runs on an estate runner set. No GitHub-hosted runner label is allowed anywhere, including here.
2. Workflow YAML stays thin. A job uses only `actions/checkout@<sha>` and the reusable workflows in this repository, each pinned by full 40-character commit SHA. No `services:`, `container:`, Environments, OIDC, Actions cache or artifacts, `pull_request_target`, `workflow_run` or ghcr.io.
3. Logic lives in scripts: the calling repository's own (`ci/*.sh`) or the ones in `$ESTATE_CI`. A workflow step is one command.

The only Actions secret the lint allows is `PIN_BOT_PRIVATE_KEY`, with variable `PIN_BOT_CLIENT_ID`, for the GitHub App `vallery-pin-bot`. Only `pin-bump.yml` and `renovate.yml` use it, so that the pull requests they open start their own checks.

## Reusable workflows

- `checks.yml`: check out and run one repository command, optionally inside the repository's CI image or with a Postgres sidecar.
- `lint.yml`: the workflow lint for the three rules, the image lint (deploy manifests must reference `@sha256` digests) and the `version-register.json` schema check.
- `image-build.yml`: Kaniko build, Trivy scan with the CVE gate, and SBOM; it pushes only on a `<repo>-build` set.
- `build-plan.yml`: choose which images to build from the caller's `ci/images.tsv` and the changed paths.
- `pin-bump.yml`: open the pull request that pins a new image digest in the caller's manifests.
- `mirror.yml`: copy the upstream images listed in the caller's `mirrors.txt` into the registry, keeping their digests.
- `kustomize-validate.yml`: `kustomize build` and `kubeconform -strict` of the given roots.
- `kustomize-exec-guard.yml`: refuse any Kustomize exec plugin other than KSOPS.
- `secret-scan.yml`: gitleaks over the caller repository.
- `orphan-redispatch.yml`: find jobs left queued with no runner after a listener restart and re-run them.
- `retention-projection.yml`: fail a pull request that changes a retention setting without a capacity projection in its body. No workflow in this repository calls it.

A caller pins these by SHA and moves the pin itself; Renovate is configured not to. Change a workflow's interface by adding an input or a new file, not by changing the meaning of an existing input, so that a caller can move its pin without reading the diff.

## Build, test, deploy, verify

**Checks.** The required checks are `lint / lint` (`bash scripts/self-test.sh`: workflow lint, every fixture suite under `tests/`, actionlint, shellcheck and `render_runners.py --check`) and `foundation / foundation` (`bash scripts/foundation-check.sh`: kustomize build and kubeconform of `deploy/foundation`). `secret-scan / gitleaks` and `planner / planner` also run. Locally, run the one fixture suite for the script you changed, for example `python3 tests/lint/run.py`, and `python3 scripts/render_runners.py --check`.

**Runner sets are generated.** Edit `runners.yaml`, run `python3 scripts/render_runners.py`, and commit both; CI fails on stale output. Never edit `deploy/runners/apps/` by hand. A set's name is its `runs-on` label: `<repo>-ci`, `-pg`, `-build`, `-buildcheck`, `-ops` or `-ci-<variant>`.

**Delivery.** Three Argo CD Applications, declared in jvallery/estate-platform `gitops/applications/local-staging/estate-ci-applications.yaml`, deliver this repository:

- `estate-ci-foundation` (`deploy/foundation`) and `estate-ci-arc-systems` (`deploy/arc-systems`) sync automatically from `main`. A merge is live within minutes, and every runner Pod started after that mounts the new scripts.
- `estate-ci-runners` (`deploy/runners/apps`, which includes `arc-controller`) syncs automatically but is pinned to a commit SHA and to an explicit `directory.include` file list. A merged `runners.yaml` change is not live until you move that pin: set `targetRevision` and the `agent-fleet.vallery.net/source-revision` annotation to the estate-ci commit, regenerate the include list from `ls deploy/runners/apps/*.yaml` at that commit (without `kustomization.yaml`; a set that is not listed is not delivered), merge, and deliver it as the estate guide describes for a pinned Application.
- The runner Secrets (`arc-github-app-jvallery`, `zot-pull`, `zot-push-<repo>`) are in jvallery/estate-platform `secrets/estate-ci/`, delivered by `estate-ci-secrets`.

**Images.** A push to `main` that changes `images/estate-ci-toolbox/` builds and pushes the toolbox, and the `pin` job of `build.yml` then opens a pull request that rewrites its digest in `runners.yaml` and the rendered sets; merge that and move the `estate-ci-runners` pin to roll it out. `ci-tools` is built by dispatching `build.yml` on `main`; pin its new digest in the `job_image` rows of `runners.yaml` yourself. Add a new base image to `mirrors.txt` and merge that before the Dockerfile that uses it. After a CVE-gate block, follow "After a cve-gate block" in the README.

**Onboarding a repository** (full recipe: [docs/MIGRATION-RUNBOOK.md](docs/MIGRATION-RUNBOOK.md)). Add the repository to `runners.yaml`, render, raise `deploy/foundation/quota.yaml` in the same pull request if the new sets need it, merge, and move the `estate-ci-runners` pin with the new files added to its include list. In the repository, copy `docs/examples/ci.yml` (and `build.yml` if it builds images) with every `uses:` pinned to an estate-ci commit SHA. Then make the checks required by job context (`<job id> / <name>`, GitHub Actions app id 15368) in the repository's ruleset or branch protection, and record them in jvallery/estate-platform `ownership/record/branch-protection-baseline.yaml`. A repository that builds images also needs a registry principal `ci-<repo>` from jvallery/storage-infra and the Secret `ci-runners/zot-push-<repo>` in jvallery/estate-platform. Only jvallery repositories get runners.

**Prove it is live.**

```sh
kubectl --context vallery -n ci-runners get autoscalingrunnersets            # the set exists with the expected maximum
kubectl --context vallery -n arc-systems get pods                            # controller 2/2 and one listener per set
gh run list --repo jvallery/<repo> --limit 5                                 # a real job was picked up and finished
```

## Hazards

- A script under `deploy/foundation/bin/` reaches every runner in the estate minutes after it merges. A broken `job-started-guard.sh` or sidecar script stops every job, including the checks on the pull request that would fix it. Run the script's fixture suite before merging; the recovery is "Break-glass" in [docs/adr/0005-security-model.md](docs/adr/0005-security-model.md).
- Moving the `estate-ci-runners` pin delivers every runner change between the old and the new commit, not only yours. Read `git diff <old>..<new> -- deploy/runners/apps` first. A set rendered with `max: 0` gets no runners, so pinning to a commit that pauses sets stops CI for those repositories.
- `arc-controller` owns the four ARC CustomResourceDefinitions. Keep its Application name, release name (`arc`) and values unchanged, and never delete or prune it: removing the CRDs deletes every runner set. Changing its image restarts the controller and every listener.
- The controller deletes any runner set whose chart minor version differs from its own. Move `defaults.chart_version` and `controller.chart_version` in the order the comments in `runners.yaml` give, and expect no runners between the two steps.
- Every runner-set change re-creates that set's listener, and jobs already queued for it may never get a runner. Re-run them with `gh run rerun <run-id> --failed`.
- The workers are memory-tight. `deploy/foundation/quota.yaml` is the hard envelope for all runner Pods; raise it only when the workers have free memory and CPU requests now (`kubectl --context vallery describe node vm-k3s-node4` through `node8`), and note the measurement in the file header. `render_runners.py` refuses a quota above the recorded per-worker budget.
- node8 runs the camera recorder (Frigate) and advertises only 4 runner slots. The hot guard (`hot-guard.py`) removes runners from it when camera processing degrades. Do not raise its slots or weaken the guard to gain capacity.
- Argo CD never prunes here. Removing a set from `runners.yaml` leaves its Application and `AutoscalingRunnerSet` running; delete those two objects by name after the pin is delivered.
- A `<repo>-build` set accepts only a push to the default branch or a dispatch on it, and fork pull requests are refused on every set. A push build therefore cannot be tested from a pull request; use the `-buildcheck` set, which builds without a credential.
- Runner Pods have no Kubernetes API token and may reach only DNS, public addresses on ports 80 and 443, the apps ingress and the registry. A job that needs anything else needs a change to `deploy/foundation/networkpolicies.yaml`, not a workaround in the job.

## Neighbours

- jvallery/estate-platform: the Argo CD Applications and AppProjects for this repository, the runner Secrets (`secrets/estate-ci/`), the `ci-*` PriorityClasses, the CVE exception list (`security/estate-security-report/findings-exceptions.json`) and the branch-protection baseline.
- jvallery/storage-infra: the image registry, each repository's push principal `ci-<repo>`, and image retention.
- jvallery/monitoring-stack: the `EstateCI*` alerts and the CI dashboards.
- jvallery/proxmox-infrastructure: the worker nodes and their capacity. Its node maintenance tool evicts idle runners and waits for busy ones.
