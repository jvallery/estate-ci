# Onboarding a repository to estate-ci

The recipe for giving a jvallery repository runner sets, workflows, required
checks and, if it builds images, a build and digest-pin flow. Every step can be
undone on its own (see "Rollback per step").

Notation: `<owner>/<repo>`, `<r>` = repo name lowercased with `_` and `.` → `-`
(the scale-set prefix).

Only jvallery repositories get runners (ADR-0002): there are no
VerdifyConsultancy or organization-level sets. A VerdifyConsultancy repository
that needs CI is transferred to jvallery first, then onboarded here. Commits to
repositories still in VerdifyConsultancy must not contain operator-machine
paths.

## 0. Preconditions

- `estate-ci-foundation`, `estate-ci-runners` and `estate-ci-secrets` are
  Synced and Healthy, and
  `kubectl -n ci-runners get secret arc-github-app-jvallery zot-pull` lists both.
- The GitHub App `vallery-ci-runners` (installation 179108157) covers the
  repository. It is installed on all jvallery repositories; if a new set's
  listener in `arc-systems` cannot register, check the installation first.
- CI never deploys directly. The repository's own Argo CD Application is
  unchanged by this work: merged digest pins are what deploy.

## 1. Plan the profiles

List what the repository's checks need and save its current required checks,
which are your rollback for step 5:
`gh api repos/<owner>/<repo>/rulesets` and
`gh api repos/<owner>/<repo>/branches/main/protection`.

| Check | estate-ci |
|---|---|
| command in a repo CI image (`image: registry.vallery.net/...-ci@sha256`) | `ci` profile with `job_image:` that digest, `checks.yml` `in-job-image: true` |
| plain command (python/node/ruby on the toolbox) | `ci` profile, `checks.yml` |
| Postgres-backed suite | `pg` profile, `runner: <r>-pg`, `postgres: true`; shard with a matrix of `checks.yml` calls, each under 15 min |
| image build | `build` and `buildcheck` profiles, `image-build.yml`; plan the matrix with `build-plan.yml` |
| kustomize build + kubeconform | `kustomize-validate.yml` (repo-specific checks stay repo scripts via `checks.yml`) |

## 2. Registry principal (only if the repo builds images)

Owner: jvallery/storage-infra. Follow its zot principal procedure
(`docs/runbooks/sentinel-frigate-import-zot.md` shape):

1. storage-infra PR: exact zot ACL rule(s) for the repo's image paths, e.g.
   `jvallery/<repo>` and `jvallery/<repo>-*`, with policies
   `ci-<r>`: read/create/update and `cluster-pull`: read. No delete, no admin.
2. Generate the password in a private, mode-0600 process; add one bcrypt
   htpasswd entry `ci-<r>` to `registry-origin/zot-origin-htpasswd` with a
   resourceVersion-fenced JSON patch; reconcile only the registry-origin
   ConfigMap/StatefulSet at the merged revision. zot restarts, so first check
   that no build is pushing; afterwards check one Ready pod and an unchanged
   PVC UID.
3. Seal the consumer copy without printing it: a
   `kubernetes.io/dockerconfigjson` Secret `zot-push-<r>` (key `.dockerconfigjson`,
   auth for `registry-origin.registry-origin.svc.cluster.local:5000`) as
   `secret-zot-push-<r>.enc.yaml` in jvallery/estate-platform
   `secrets/estate-ci/`, added to that bundle's `secret-generator.yaml` and
   delivered by `estate-ci-secrets`, which tracks `main`: the merge delivers it.
   The renderer mounts it only into `<r>-build` runner containers. No GitHub
   secrets or environments are involved.
4. Prove it without reading the value: an upload start to the repo's path is
   202; to another repo's path 401/403.

## 3. Runners (PR to jvallery/estate-ci)

Add the repo to `runners.yaml` (`profiles: {ci: {...}, pg: {...}, build: {...}, buildcheck: {...}}`),
run `python3 scripts/render_runners.py`, and open a PR. In the same PR, raise
`deploy/foundation/quota.yaml` by what the new sets need. Merge when the
required checks are green.

`estate-ci-runners` is pinned to an estate-ci commit and to an explicit
`directory.include` file list in jvallery/estate-platform
`gitops/applications/local-staging/estate-ci-applications.yaml`. Move the pin
to your merge commit (`targetRevision` and the
`agent-fleet.vallery.net/source-revision` annotation), add the new
`deploy/runners/apps/<r>-*.yaml` files to the include list, and deliver it.
The pin delivers every runner change between the old and the new commit, so
read `git diff <old>..<new> -- deploy/runners/apps` first.

Check `kubectl -n ci-runners get autoscalingrunnerset <r>-ci` and the listener
in `arc-systems`.

During planned worker maintenance, the maintenance tool evicts an idle
EphemeralRunner Pod (one with no assigned job) and ARC replaces it on another
worker; runners with an assigned job are waited for. See the
[planned maintenance runbook](https://github.com/jvallery/proxmox-infrastructure/blob/main/config/k3s-planned-maintenance/README.md).

## 4. Workflows (PR to the repo)

Copy `docs/examples/ci.yml` (and `build.yml` if the repo builds) into
`.github/workflows/`, with every `uses:` pinned to the current estate-ci SHA.
Keep the job ids and names stable: they become the required check contexts
(`<job id> / <name>`).

- Every workflow must pass the estate-ci lint (`lint.yml`, the `workflow-lint`
  job in the example; locally `python3 deploy/foundation/bin/lint-workflows.py <repo>`
  from an estate-ci checkout): ARC labels only, `actions/checkout@<sha>` and
  `jvallery/estate-ci/...@<sha>` only, no `services:`/`container:`,
  `environment:`, `id-token: write`, `pull_request_target`/`workflow_run`,
  Actions cache/artifacts or ghcr.io. Warnings (non-portable contexts,
  `secrets.*`, long inline shell) become errors with `strict: true`. Replace
  `actions/setup-*`, cache and marketplace actions with repo scripts or a
  `job_image` that already contains the toolchain.
- Concurrency: `cancel-in-progress` on PRs only (see the example). A push to a
  PR supersedes its run; a moved `main` does not rerun anything.
- An infrastructure failure: `gh run rerun <run-id> --failed`.
- Public repos: `scripts/fork-policy.sh check <owner/repo>` (`gh` admin
  auth) must pass; `apply` sets approval for all outside collaborators.

## 5. Required checks

One API write per repo:

- Ruleset repos: `gh api -X PUT repos/<owner>/<repo>/rulesets/<id>` with the
  `required_status_checks` rule listing the new contexts and
  `integration_id: 15368` (GitHub Actions).
- Classic protection: `gh api -X PATCH
  repos/<owner>/<repo>/branches/main/protection/required_status_checks` with
  `{"strict": false, "checks": [{"context": "lint / lint", "app_id": 15368}, ...]}`.
- Repos whose GitHub plan cannot enforce checks (`jvallery/options-tracker`):
  skip the write. The checks are advisory there: merge only when they are
  green.

Keep "require branches to be up to date" **off**. Record the expected contexts
in jvallery/estate-platform `ownership/record/branch-protection-baseline.yaml`.
Rollback: re-PUT the JSON saved in step 1.

Open a no-op PR to prove the new contexts gate and pass.

## 6. Build and pin (only if the repo builds images)

1. Set the repository's Actions secret `PIN_BOT_PRIVATE_KEY` and variable
   `PIN_BOT_CLIENT_ID` (GitHub App `vallery-pin-bot`), so that pin PRs start
   their own checks. Without them the pin PR stays blocked on required checks.
2. Repo PR: `build.yml` with `push: true` and the `pin` job. Merge.
3. Verify end to end on one real change:
   - `build` run green; the step summary shows `registry.vallery.net/<image>@sha256:…`;
     `crane digest` of `:gha-<sha12>` equals it.
   - The pin PR exists and its checks are green; merge it.
   - Argo CD: the repo's Application is Synced and Healthy at the merge
     revision and the workload's `imageID` is the new digest. Probe the
     changed behaviour live.

## Forgejo/Gitea Actions portability checklist

Run through this for every onboarded repo; it is what keeps a later forge
move a re-registration rather than a rewrite.

- [ ] Every `runs-on` is an estate ARC label; `lint.yml` passes (ideally with `strict: true`).
- [ ] Only `actions/checkout` and `jvallery/estate-ci` workflows in `uses:`
      (Forgejo mirrors `actions/checkout`; nothing else needs a GitHub-hosted action).
- [ ] All logic is in repo scripts (`ci/*.sh`, Makefile, task file) or
      `$ESTATE_CI/*.sh`; each workflow step is one command.
- [ ] No GitHub Environments, OIDC, Actions cache, artifacts, GHCR, Pages,
      `pull_request_target` or `workflow_run` in the build path. Outputs that
      persist go to registry.vallery.net or Garage.
- [ ] Step outputs via `$GITHUB_OUTPUT` only (Forgejo supports it); contexts
      limited to `github.*`, `inputs.*`, `needs.*`, `steps.*`, `secrets.*`.
- [ ] Secrets the build needs come from Kubernetes (runner Pod mounts), not from
      forge secret stores; the job token is used only for git push and
      `forge-api.sh` calls.
- [ ] Pin PRs use `pin-bump.sh` (plain git + `forge-api.sh`; `FORGE=forgejo`).
- [ ] Cross-repo reusable workflows: if the target forge cannot call them,
      vendor the job (checkout + one `run:` line calling `$ESTATE_CI/...`).
      Every estate-ci workflow header shows its vendored form.
- [ ] `concurrency` / `cancel-in-progress`: verify on the target forge; where
      unsupported, superseded PR runs finish instead of cancelling (harmless,
      costs runner time).
- [ ] Required checks are listed by job name, so a forge's branch protection
      can name the same contexts.

## Rollback per step

| Step | Undo |
|---|---|
| 2 | Revert the ACL PR, re-reconcile registry-origin, remove the htpasswd entry after no consumer remains, remove `zot-push-<r>` from the estate-ci secrets bundle. |
| 3 | Revert the estate-ci PR and move the pin back. Argo CD leaves the set (prune is off): delete the Application `estate-ci-runner-<r>-*` and its `AutoscalingRunnerSet` by name. |
| 4 | Delete the workflow files. |
| 5 | Re-PUT the saved ruleset/protection JSON. |
| 6 | Set `push: false`. |
