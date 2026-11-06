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
  `kubectl -n ci-runners get secret arc-github-app-jvallery arc-github-app-verdifyconsultancy zot-pull`.
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
3. Store the consumer copy in the repo, never printing it:
   `gh api -X PUT repos/<owner>/<repo>/environments/registry -f 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'`,
   `gh api -X POST repos/<owner>/<repo>/environments/registry/deployment-branch-policies -f name=main`,
   then pipe the values into
   `gh secret set ESTATE_CI_REGISTRY_USERNAME --env registry -R <owner>/<repo>` and
   `... ESTATE_CI_REGISTRY_PASSWORD ...`.
4. Prove value-blind: an upload start to the repo's path is 202; to another
   repo's path 401/403.

## 3. Runners (PR to jvallery/estate-ci)

Add the repo to `runners.yaml` (`profiles: {ci: {...}, pg: {...}, build: {...}}`),
run `python3 scripts/render_runners.py`, and open a PR. In the same PR, raise
`deploy/foundation/quota.yaml` by what the new sets need, and write the matching
decrease of the repo's native budget in the PR body (it lands in step 6).
Merge when `self-test` is green. `estate-ci-runners` auto-syncs (prune off);
check `kubectl -n ci-runners get autoscalingrunnerset <r>-ci` and the listener
in `arc-systems`.

VerdifyConsultancy: repos share the org-level set (`org: VerdifyConsultancy`,
`name: verdify`); callers pass `runner: verdify-ci` (or `verdify-build`).
Commits to VerdifyConsultancy repos must not contain operator-machine paths or
the string naming the fleet repo.

## 4. Workflows in parallel (PR to the repo)

Copy `docs/examples/ci.yml` (and `build.yml` if the repo builds) into
`.github/workflows/`, with every `uses:` pinned to the current estate-ci SHA.
Keep the job ids and names stable: they become the required check contexts
(`<job id> / <name>`). In this phase:

- `ci.yml` runs on PRs and `main` next to native CI. Native stays required.
- `build.yml` is either absent or `push: false` (native still pushes and pins).
- Concurrency: `cancel-in-progress` on PRs only (see the example). That replaces
  native's supersession and re-plan logic; a moved `main` does not rerun anything (L1).

**Parallel-run gate:** at least 3 PRs (or 24 h of `main` pushes, whichever
comes first) where every estate-ci check finished and matched native's verdict.
A red estate-ci check with green native is a migration bug: fix it before
continuing. Infra failures: `gh run rerun --failed` once, then report (L2).

## 5. Switch required checks

One API write per repo, with the before/after JSON in receipts:

- Ruleset repos: `gh api -X PUT repos/<owner>/<repo>/rulesets/<id>` with the
  `required_status_checks` rule listing the new contexts and
  `integration_id: 15368` (GitHub Actions).
- Classic protection (e.g. weather): `gh api -X PATCH
  repos/<owner>/<repo>/branches/main/protection/required_status_checks` with
  `{"strict": false, "checks": [{"context": "lint / lint", "app_id": 15368}, ...]}`.
- Repos where the plan cannot enforce checks (VerdifyConsultancy private repos,
  `jvallery/options-tracker`, see J-2): skip the write. The checks are
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

## Rollback per step

| Step | Undo |
|---|---|
| 2 | Revert the ACL PR, re-reconcile registry-origin, remove the htpasswd entry after no consumer remains, delete the environment secrets. |
| 3 | Revert the estate-ci PR; Argo leaves the set (prune off): delete the Application `estate-ci-runner-<r>-*` by UID. |
| 4 | Delete the workflow files. |
| 5 | Re-PUT the saved ruleset/protection JSON. |
| 6 | Re-enable the native catalog row (agents PR) and set `push: false`. |
