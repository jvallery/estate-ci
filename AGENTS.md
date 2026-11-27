# estate-ci

This repository is a CI/CD system for GitHub Actions on self-hosted runners: the reusable workflows that the jvallery repositories call, the scripts those workflows run, and the definitions of the Actions Runner Controller (ARC) runner sets that execute every job. It is public. Nothing committed here may contain a credential, a kubeconfig, a path on someone's workstation or an internal IP address, and every commit, branch and pull request stays readable by anyone. Stage files by name and read `git diff --cached` before committing.

`AGENTS.md` is the one agent instruction file here; there is no `CLAUDE.md`.

## This repository

- `.github/workflows/` holds the reusable workflows listed below and this repository's own workflows (`self-test.yml`, `build.yml`, `mirror-images.yml`, `renovate.yml`).
- `deploy/foundation/bin/` holds all CI logic. It is delivered as ConfigMap `estate-ci-scripts` and mounted at `/opt/estate-ci` (`$ESTATE_CI`) in every runner Pod, so a workflow step calls `"$ESTATE_CI/<script>"`.
- `runners.yaml` is the runner registry: which repository gets which runner sets, at what size. It is the only input to `scripts/render_runners.py`, which writes `deploy/runners/apps/` (one Argo CD Application per set, plus `arc-controller` and `estate-ci-arc-systems`).
- `deploy/foundation/` is the rest of namespace `ci-runners` (namespace, `quota.yaml`, `networkpolicies.yaml`, the hot guard and the runner-slot advertiser); `deploy/arc-systems/` holds the ARC controller's support objects.
- `images/` holds the two job images (`estate-ci-toolbox`, `ci-tools`), `mirrors.txt` the upstream images this repository mirrors, and `renovate/` the Renovate policy (`global.json`) and the list of covered repositories (`repositories.json`).
- Design: [README.md](README.md). Onboarding recipe: [docs/MIGRATION-RUNBOOK.md](docs/MIGRATION-RUNBOOK.md). Current procedures: [docs/runbooks/README.md](docs/runbooks/README.md). The decision records in [docs/adr/](docs/adr/README.md) are the history of the design; where one disagrees with this file or the README, this file and the README are current.

## The three CI rules

`lint.yml` runs `deploy/foundation/bin/lint-workflows.py` in every repository that calls it.

1. **R1, estate runners only.** Every job names exactly one estate runner set in `runs-on`. GitHub-hosted and generic labels (`ubuntu-*`, `self-hosted`, `linux` and so on) fail, here too.
2. **R2, thin workflow YAML.** `uses:` is only `actions/checkout@<40-hex>`, `jvallery/estate-ci/...@<40-hex>` or a local `./` path. No `services:`, `container:`, Environments, `id-token: write` (OIDC), Actions cache or artifacts, `pull_request_target`, `workflow_run` or ghcr.io. Logic lives in scripts (the caller's own `ci/*.sh` or `$ESTATE_CI`); a `run:` block over 15 lines is a warning.
3. **R3, forge portability.** Expressions read only the portable contexts in `ALLOWED_CONTEXTS` of the script (a few `github.*` fields, and `inputs`, `needs`, `matrix`, `steps`, `env`, `vars`, `runner`), and the only secrets are `GITHUB_TOKEN` and `PIN_BOT_PRIVATE_KEY`. These are warnings, and errors with `strict: true`. In this repository every reusable workflow also carries a "Forgejo" notes header (`--require-forgejo-notes`, an error).

The one allowed Actions secret is `PIN_BOT_PRIVATE_KEY`, with variable `PIN_BOT_CLIENT_ID`, for the GitHub App `vallery-pin-bot`. Only `pin-bump.yml` and `renovate.yml` use it, so that the pull requests they open start their own checks. The same lint also runs the image lint (`lint-images.py`: manifests reference images by `@sha256` digest) and, when the caller has a `version-register.json`, its schema check. Recorded exceptions go in the caller's `.estate-ci/workflow-lint-allow.txt` (format in the script header; every row has an expiry at most 180 days out).

Run it locally from an estate-ci checkout: `python3 deploy/foundation/bin/lint-workflows.py <path to the repository>` (standard library only; add `--strict` to see what `strict: true` would fail).

## Reusable workflows

- `checks.yml`: check out and run one repository command, optionally inside the repository's CI image or with a Postgres sidecar.
- `lint.yml`: the three rules above, the image lint and the version register check.
- `image-build.yml`: Kaniko build, Trivy scan with the CVE gate, and SBOM; it pushes only on a `<repo>-build` set.
- `build-plan.yml`: choose which images to build from the caller's `ci/images.tsv` and the changed paths.
- `pin-bump.yml`: open the pull request that pins a new image digest in the caller's manifests.
- `mirror.yml`: copy the upstream images listed in the caller's `mirrors.txt` into the registry, keeping their digests.
- `kustomize-validate.yml`: `kustomize build` and `kubeconform -strict` of the given roots.
- `kustomize-exec-guard.yml`: refuse any Kustomize exec plugin other than KSOPS.
- `secret-scan.yml`: gitleaks over the caller repository, with the caller's exact-value `.gitleaks.toml`.
- `orphan-redispatch.yml`: find jobs left queued with no runner after a listener restart and re-run them.

One check has no workflow of its own: `$ESTATE_CI/owner-root-children.py --config <file>` checks the child Applications that a nested Argo CD root reads from the calling repository (names, projects, source repository, no prune). A repository runs it as one step of a check that is already required; the script header documents the config file.

**Pinning.** A caller copies [docs/examples/ci.yml](docs/examples/ci.yml) (and [build.yml](docs/examples/build.yml) if it builds images) and pins every `uses: jvallery/estate-ci/.github/workflows/<file>@<sha>` to a full 40-character estate-ci commit SHA. The caller moves its own pin; Renovate is configured not to. Change a workflow's interface by adding an input or a new file, never by changing the meaning of an existing input, so that a caller can move its pin without reading the diff. Check contexts are `<job id> / <name>`, so keep job ids and names stable once they are required.

## Build, test, deploy, verify

**Contributing.** Branch from `main` and open a pull request; `main` accepts changes only by pull request, squash merge only, with no bypass. The required checks are `lint / lint` (`bash scripts/self-test.sh`: workflow lint, every fixture suite under `tests/`, actionlint, shellcheck and `render_runners.py --check`) and `foundation / foundation` (`bash scripts/foundation-check.sh`: kustomize build and kubeconform of `deploy/foundation`). `secret-scan / gitleaks` and `planner / planner` also run. A branch need not be up to date with `main`. Pull requests from forks never run on the runners (the job-started guard refuses them); a maintainer re-pushes a reviewed fork contribution to a branch.

**Local checks.** Run the fixture suite for the script you changed, for example `python3 tests/lint/run.py`, and `python3 scripts/render_runners.py --check` (needs PyYAML; `scripts/self-test.sh` pins the versions CI installs). The suites are listed in `scripts/self-test.sh` and in the `planner` job of `.github/workflows/self-test.yml`.

**Runner sets are generated.** Edit `runners.yaml`, run `python3 scripts/render_runners.py`, and commit both; CI fails on stale output. Never edit `deploy/runners/apps/` by hand. A set's name is its `runs-on` label: `<repo>-ci`, `-pg`, `-build`, `-buildcheck`, `-ops` or `-ci-<variant>`.

**Delivery.** Argo CD applies this repository from Git. `deploy/foundation` and `deploy/arc-systems` follow `main`: a merge is live within minutes, and every runner Pod started after it mounts the new scripts. The runner sets in `deploy/runners/apps` are delivered from a pinned estate-ci commit with an explicit file list, so a merged `runners.yaml` change is live only after that pin moves to a commit that contains it.

**Images.** A push to `main` that changes `images/estate-ci-toolbox/` builds and pushes the toolbox, and the `pin` job of `build.yml` then opens a pull request that rewrites its digest in `runners.yaml` and the rendered sets. `ci-tools` is built by dispatching `build.yml` on `main`; pin its new digest in the `job_image` rows of `runners.yaml`. Add a new base image to `mirrors.txt` and merge that before the Dockerfile that uses it. After a CVE-gate block, follow "After a cve-gate block" in the README.

**Onboarding a repository**: [docs/MIGRATION-RUNBOOK.md](docs/MIGRATION-RUNBOOK.md). Only jvallery repositories get runner sets.

**Verify.** `gh run list --repo jvallery/<repo> --limit 5` shows that a real job was picked up and finished. A change to a reusable workflow is proven when a caller pinned to the new commit runs green.

## Hazards

- A script under `deploy/foundation/bin/` reaches every runner minutes after it merges. A broken `job-started-guard.sh` or sidecar script stops every job, including the checks on the pull request that would fix it. Run the script's fixture suite before merging. If it happens, the repair restores the last good scripts in the cluster outside CI and then merges the fix through its normal checks; nobody edits or bypasses the ruleset to get the fix in.
- A caller pins a workflow by SHA, but every runner mounts the scripts from `main`. A script change must stay compatible with every workflow commit that callers still pin.
- Moving the runner-set pin delivers every runner change between the old and the new commit, not only yours. Read `git diff <old>..<new> -- deploy/runners/apps` first. A set rendered with `max: 0` gets no runners.
- `arc-controller` owns the four ARC CustomResourceDefinitions. Keep its Application name, release name (`arc`) and values unchanged, and never delete or prune it: removing the CRDs deletes every runner set. Changing its image restarts the controller and every listener.
- The controller deletes any runner set whose chart minor version differs from its own. Move `defaults.chart_version` and `controller.chart_version` in the order the comments in `runners.yaml` give, and expect no runners between the two steps.
- Every runner-set change re-creates that set's listener, and jobs already queued for it may never get a runner. Re-run them with `gh run rerun <run-id> --failed`, or let `orphan-redispatch.yml` find them.
- Argo CD never prunes here. Removing a set from `runners.yaml` leaves its Application and `AutoscalingRunnerSet` running until they are deleted by name.
- `deploy/foundation/quota.yaml` is the hard envelope for all runner Pods, and `render_runners.py` refuses a quota above the recorded per-worker budget. Raise it only with measured free capacity, noted in the file header.
- One worker runs a latency-sensitive workload and advertises fewer runner slots; the hot guard (`hot-guard.py`) removes runners from it when that workload degrades. Do not raise its slots or weaken the guard to gain capacity.
- A `<repo>-build` set accepts only a push to the default branch or a dispatch on it. A push build therefore cannot be tested from a pull request; use the `-buildcheck` set, which builds without a credential.
- Runner Pods have no Kubernetes API token and may reach only DNS, public addresses on ports 80 and 443, the apps ingress and the registry. A job that needs anything else needs a change to `deploy/foundation/networkpolicies.yaml`, not a workaround in the job.

## Neighbours

The Argo CD Applications that deliver this repository, the runner Secrets, the image registry and its push principals, the CI alerts and dashboards, and the worker nodes are owned by other repositories of the estate, which are private. Estate maintainers keep the operating notes for this repository with the rest of the estate's documentation.
