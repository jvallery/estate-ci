# Migrating a repository to estate-ci

The per-repo recipe for jvallery/agents#6092 (FLEET-DECOM). One repo at a time
per lane, following the estate operating contract and steer (L1 to L12). The
order is: add the runners and workflows, run them in parallel with native CI,
switch the required checks, turn on build/pin, then remove the native-ci
catalog entry. Every step can be undone on its own.

Notation: `<owner>/<repo>`, `<r>` = repo name lowercased with `_` and `.` → `-`
(the scale-set prefix). Receipts go in your lane's receipts directory.

## 0. Preconditions (once, estate-wide)

- `estate-ci-foundation`, `estate-ci-runners` and `estate-ci-secrets` Synced/Healthy;
  `kubectl -n ci-runners get secret arc-github-app-jvallery zot-pull`.
- The repo's own Argo Application, if any, is unchanged by this work: CI never deploys
  directly; merged pins do.

## 1. Inventory (read-only)

From the repo's native catalog row (`jvallery/agents`
`control-plane/agent-fleet-control/registry/repos/gh-<id>.yaml`, `ci_submission`):
copy the validation steps (name, image, command), the build profiles
(dockerfile, context, target, build args, path filters) and the delivery mode.
Note the required checks today:
`gh api repos/<owner>/<repo>/rulesets` and
`gh api repos/<owner>/<repo>/branches/main/protection` (save both as JSON:
these are your rollback).

Map each native step to a profile:

| Native step | estate-ci |
|---|---|
| command in a repo CI image (`image: registry.vallery.net/...-ci@sha256`) | `ci` profile with `job_image:` that digest, `checks.yml` `in-job-image: true` |
| plain command (python/node/ruby on the toolbox) | `ci` profile, `checks.yml` (use `actions/setup-*` steps in a custom job if tools are missing) |
| Postgres-backed suite | `pg` profile, `runner: <r>-pg`, `postgres: true`; shard with a matrix of `checks.yml` calls, each under 15 min |
| image build | `build` profile, `image-build.yml` |

## 2. Registry principal (only if the repo builds images)

Owner: jvallery/storage-infra. Follow its zot principal procedure
(`docs/runbooks/sentinel-frigate-import-zot.md` shape):

1. storage-infra PR: exact zot ACL rule(s) for the repo's image paths, e.g.
   `jvallery/<repo>` and `jvallery/<repo>-*`, with policies
   `ci-<r>`: read/create/update, `cluster-pull`: read, and, **until step 6**,
   `jvallery-ci`: read/create/update (the native writer the longer rule would
   otherwise lock out). No delete, no admin.
2. Generate the password in a private, mode-0600 process; add one bcrypt
   htpasswd entry `ci-<r>` to `registry-origin/zot-origin-htpasswd` with a
   resourceVersion-fenced JSON patch; reconcile only the registry-origin
   ConfigMap/StatefulSet at the merged revision (zot restarts: announce in steer,
   check no other lane is pushing, one Ready pod, PVC UID unchanged).
3. Seal the consumer copy into central KSOPS, never printing it: a
   `kubernetes.io/dockerconfigjson` Secret `zot-push-<r>` (key `.dockerconfigjson`,
   auth for `registry-origin.registry-origin.svc.cluster.local:5000`) in
   jvallery/agents `platform/gitops/secrets-ksops/estate-ci/`, added to that
   bundle's generator (agents PR, COORD-ASK; delivered by `estate-ci-secrets`).
   The renderer mounts it only into `<r>-build` runner containers. No GitHub
   secrets or environments are involved (R2).
4. Prove value-blind: an upload start to the repo's path is 202; to another
   repo's path 401/403.

## 3. Runners (PR to jvallery/estate-ci)

Add the repo to `runners.yaml` (`profiles: {ci: {...}, pg: {...}, build: {...}, buildcheck: {...}}`),
run `python3 scripts/render_runners.py`, and open a PR. In the same PR, raise
`deploy/foundation/quota.yaml` by what the new sets need, and write the matching
decrease of the repo's native budget in the PR body (it lands in step 6).
Merge when `self-test` is green. `estate-ci-runners` auto-syncs (prune off);
check `kubectl -n ci-runners get autoscalingrunnerset <r>-ci` and the listener
in `arc-systems`.

jvallery only (D19, ADR-0002): there are no VerdifyConsultancy or org-level
sets. A VerdifyConsultancy repository that needs CI is transferred to jvallery
first (REPO-MOVE), then onboarded here like any jvallery repository.
Commits to repositories still in VerdifyConsultancy must not contain
operator-machine paths or the string naming the fleet repo.

## 4. Workflows in parallel (PR to the repo)

Copy `docs/examples/ci.yml` (and `build.yml` if the repo builds) into
`.github/workflows/`, with every `uses:` pinned to the current estate-ci SHA.
Keep the job ids and names stable: they become the required check contexts
(`<job id> / <name>`). In this phase:

- `ci.yml` runs on PRs and `main` next to native CI. Native stays required.
- `build.yml` is either absent or `push: false` on `<r>-buildcheck` (native still pushes and pins).
- Every workflow must pass the estate-ci lint (`lint.yml`, the `workflow-lint`
  job in the example; locally `python3 deploy/foundation/bin/lint-workflows.py <repo>`
  from an estate-ci checkout): ARC labels only, `actions/checkout@<sha>` and
  `jvallery/estate-ci/...@<sha>` only, no `services:`/`container:`,
  `environment:`, `id-token: write`, `pull_request_target`/`workflow_run`,
  Actions cache/artifacts or ghcr.io. Warnings (non-portable contexts,
  `secrets.*`, long inline shell) become errors with `strict: true`. Replace
  `actions/setup-*`, cache and marketplace actions with repo scripts or a
  `job_image` that already contains the toolchain.
- Concurrency: `cancel-in-progress` on PRs only (see the example). That replaces
  native's supersession and re-plan logic; a moved `main` does not rerun anything (L1).

**Parallel-run gate (fast path, D19, ADR-0008):** merge-gated or image-building
repos need 5 PRs where every estate-ci check finished and matched native's
verdict; static-only repos need 3 runs including one `main` push. There is no
minimum duration (no 48-hour wait).
A red estate-ci check with green native is a migration bug: fix it before
continuing. Infra failures: `gh run rerun --failed` once, then report (L2).

- Public repos: `scripts/fork-policy.sh check <owner/repo>` (operator, `gh` admin
  auth) must pass; `apply` sets approval for all outside collaborators.

## 5. Switch required checks

One API write per repo, with the before/after JSON in receipts:

- Ruleset repos: `gh api -X PUT repos/<owner>/<repo>/rulesets/<id>` with the
  `required_status_checks` rule listing the new contexts and
  `integration_id: 15368` (GitHub Actions).
- Classic protection (e.g. weather): `gh api -X PATCH
  repos/<owner>/<repo>/branches/main/protection/required_status_checks` with
  `{"strict": false, "checks": [{"context": "lint / lint", "app_id": 15368}, ...]}`.
- Repos where the plan cannot enforce checks (`jvallery/options-tracker`, see J-2;
  VerdifyConsultancy repos do not run on estate-ci until transferred, D19): skip the write. The checks are
  **advisory**: the lane merges only when they are green (compare-check), and
  states "advisory" in the PR.

Keep "require branches to be up to date" **off**. Rollback: re-PUT the saved JSON.

Open a no-op PR to prove the new contexts gate and pass.

## 6. Build, pin, and retire native for this repo

1. Repo PR: `build.yml` with `push: true` and the `pin` job. Merge.
2. jvallery/agents PR (own PR, COORD-ASK): set the repo's catalog row
   `ci_submission.activation: disabled` (and `events.activation: disabled`), so
   native stops validating, building and pinning; lower its native budget by
   what step 3 added. Remove `jvallery-ci` from the repo's zot rule
   (storage-infra PR) once native no longer pushes there.
3. Verify end to end on one real change:
   - `build` run green; the step summary shows `registry.vallery.net/<image>@sha256:…`;
     `crane digest` of `:gha-<sha12>` equals it.
   - The pin PR exists, its dispatched `ci` checks are green, merge it.
   - Argo: the repo's Application is Synced/Healthy at the merge revision and the
     workload's `imageID` is the new digest. Live probe the changed behaviour.
4. Soak 10 minutes (CI-only change, L10), then close the repo's migration issue
   with the evidence (an independent verifier closes, L9).

## 7. Clean up (after the epic's purge gate)

Remove the repo's legacy `github-runners` scale set and auth Application (if it
had one) and its native catalog row, in the agents delete-set that FLEET-DECOM
runs through COORD.

## Forgejo/Gitea Actions portability checklist (R3)

Run through this for every migrated repo; it is what keeps the later forge
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
| 2 | Revert the ACL PR, re-reconcile registry-origin, remove the htpasswd entry after no consumer remains, remove `zot-push-<r>` from the estate-ci KSOPS bundle. |
| 3 | Revert the estate-ci PR; Argo leaves the set (prune off): delete the Application `estate-ci-runner-<r>-*` by UID. |
| 4 | Delete the workflow files. |
| 5 | Re-PUT the saved ruleset/protection JSON. |
| 6 | Re-enable the native catalog row (agents PR) and set `push: false`. |
