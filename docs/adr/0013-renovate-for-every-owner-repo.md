# ADR-0013: Renovate for every owner repository

Status: Accepted for batches 0-1 (2026-11-18, estate-ci#261). Owner: estate-ci.
Later batches follow the plan below, one PR each.

## Context

STD-OPS-02 requires automated update detection for every owner repository, and
DR-51 names the detector: self-hosted Renovate on ARC. estate-ci#110 piloted it
on this repository only (`RENOVATE_REPOSITORIES="$GITHUB_REPOSITORY"`). The run
is fast (about 20 s) and green every weekday, and it has opened one real PR
(estate-ci#190).

Constraints that shape the design:

- **Identity.** Renovate needs a token that can push branches and open PRs whose
  `pull_request` workflows start, so required checks attach. The estate already
  has one such identity, the GitHub App `vallery-pin-bot` (D-52). Its key is the
  Actions secret `PIN_BOT_PRIVATE_KEY` (variable `PIN_BOT_CLIENT_ID`) in this
  repository. `app-token.py` mints installation tokens limited to one repository
  with contents and pull_requests write (Renovate passes add issues read, which
  its repository init needs), valid for 1 h. No new credential, App
  or install is created by this design.
- **Installation scope.** D-52 installed the App on selected repositories, not
  All, and deliberately kept agents, home-dotfiles and vault-personal out of its
  write reach. It is not installed in the VerdifyConsultancy organization.
  Covering a repository the App is not installed on is a Jason decision.
- **Pins.** Deployed references are digests written only by the estate-ci pin
  job (`pin-bump.yml`) or mirrored through `mirrors.txt`. Argo roots and children
  are SHA-pinned and move only in the consolidated pin PR (L4). Callers pin
  estate-ci reusable workflows by full SHA. Renovate must not compete with any of
  these writers.
- **CI etiquette.** L1: a PR whose CI is running or green is rebased only on a
  conflict. Renovate's default `rebaseWhen: auto` would rebase on every moved
  base.
- **Thin YAML (R2).** Workflow logic lives in scripts, not in `run:` blocks.

## Decision

**One explicit list.** `renovate/repositories.json` names every active owner
repository from the root AGENTS.md lane table exactly once. Each entry has a
`batch` (an integer) or `"exception"` with a reason. `active_batches` says which
batches the scheduled run covers. `scripts/renovate-repos.py check` validates
the file in `self-test`.

**One pass per repository, one token per pass.** `scripts/renovate-run.sh run`
reads the active repositories. For each one it checks that the App is installed,
mints a token for that repository only, runs Renovate in the job sidecar with
`RENOVATE_REPOSITORIES=<that repository>`, then revokes the token. The App key
stays in the runner container; the Renovate sidecar sees only the one-repository
token. A repository without an installation is logged as `not-covered` with a
warning and the run continues. A Renovate failure in any repository fails the
job, after every repository has had its pass. The log and the step summary list
every repository with its result. The job runs serially on the existing
`estate-ci-ci-renovate` set (max 1, 500m/1Gi request), so capacity is unchanged.
At about 20 s to a few minutes per repository, the full list fits in the 180 min
job timeout with a wide margin.

**Probe before each batch.** `workflow_dispatch` with `mode=probe` reports, for
every non-exception repository, whether the App is installed. It mints no token
and does not run Renovate. Its output is the exact list for widening the
installation before a batch is activated.

**Policy in one global config.** `renovate/global.json` is Renovate's
self-hosted global config (`RENOVATE_CONFIG_FILE`). Its repository options are
defaults for every covered repository:

| Setting | Value | Why |
|---|---|---|
| `requireConfig` / `onboarding` | `required` / `false` | A repository is scanned only after its own `renovate.json` lands; listing it early is harmless. No onboarding PRs. |
| `automerge`, `platformAutomerge` | `false` | Renovate proposes; merges keep the train, COORD GO and pin rules. |
| `rebaseWhen` | `conflicted` | L1: no pushes to a green or running PR for a moved base. |
| `pinDigests`; update types `digest`, `pinDigest`, `pin` | off | No new digest pins and no digest-only churn. An existing `tag@digest` or `@sha # vX` pin moves only with a version update, so SHA and digest pinning is preserved. |
| docker `registry.vallery.net/**` | disabled | First-party and mirrored zot digests belong to `pin-bump.yml` and `mirrors.txt`. |
| github-actions `jvallery/estate-ci` | disabled | Reusable-workflow SHA pins move with estate-ci changes (ADR-0006). |
| argocd git revisions | disabled | Argo SHA pins move only in the consolidated pin PR (L4). Helm chart versions in Applications stay proposable. |
| `minimumReleaseAge` + `internalChecksFilter: strict` | 7 days | Unchanged from the pilot. |
| `prConcurrentLimit` / `prHourlyLimit` | 3 / 2 per repository | Keeps the CI load and the review load small. |
| Grouping | all GitHub Actions in one PR; non-major pip/npm tool pins in one PR; each image, chart and major on its own | Images and charts are separate delivery decisions. |
| `statusCheckNames` | Renovate's own statuses off | The token has no statuses permission (estate-ci#110). |
| `gitAuthor` | `vallery-pin-bot[bot]` | Required with App tokens (estate-ci#110). |

**Schedule.** Weekdays at 09:17 UTC (unchanged). The cron is the schedule, so no
Renovate `schedule` option is set.

**Per-repository config.** Each repository's `renovate.json` only picks the
managers matching what it pins, and may add packageRules. It does not repeat the
global policy. A starting point:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "description": "Estate version detector (estate-ci#261). Policy: jvallery/estate-ci renovate/global.json.",
  "enabledManagers": ["github-actions", "kubernetes", "helm-values", "argocd", "kustomize", "dockerfile", "pip_requirements", "custom.regex"]
}
```

Each lane trims the list to its own files and adds a `custom.regex` manager for
its version register or script pins where useful. Sentinel's renovate.json
excludes the Frigate `config.yaml` (D-458). The `kubernetes` manager needs
`managerFilePatterns` for the repository's manifest paths.

## Rollout

Each step is a PR. Renovate only opens PRs, so any step is undone by removing a
batch from `active_batches` (or reverting a repository's `renovate.json`) and
closing its open `renovate/` PRs.

| Batch | Repositories | Gate to activate |
|---|---|---|
| 0 | estate-ci | Live since estate-ci#110; widened to github-actions here |
| 1 | estate-platform, storage-infra, network-infra, proxmox-infrastructure, monitoring-stack, cortex-ai-compute | Active from this PR. Each is skipped until its `renovate.json` PR merges; a `not-covered` row needs the App installed (Jason) |
| 2 | sentinel, immich-config, docs, orbit, kepler, options-tracker, weather, tictactoe, research-vault, verdify-platform, verdify-www, ImagineProposals, crm, memoryos, wordpress, sunshine_club | probe clean (or installs granted), then one estate-ci PR moving 2 into `active_batches` |
| 3 | AI_presentation, vallery-family, radio | Same as batch 2 |
| 4 | homeassistant | Enable its version detector independently after its repository configuration merges |
| exception (proposed) | agents, home-dotfiles, vault-personal, sunshine-research, VerdifyConsultancy/research-template, VerdifyConsultancy/.github | Jason decision, then a STD-OPS-02 exception row in estate-platform `docs/estate/STANDARDS.md` naming estate-ci#261 |

options-tracker has outside write collaborators, so the App key never goes there
(D-696). That does not block coverage, because the token is minted here in
estate-ci and is limited to that one repository for one pass. wordpress keeps
hosted Dependabot only under its existing exception row (D-373, review
2027-02-01); retire it once Renovate covers wordpress.

## Not in this change

- `mirrors.txt` and `runners.yaml` upstream-version detection for estate-ci
  itself. Renovate replaces a row in place, but a mirror row must be added next
  to the old one until every consumer's digest pin moves. That needs a custom
  manager design of its own.
- The estate ownership record entry in jvallery/estate-platform
  (`ownership/record/`), which is updated when batch 1 is live.
