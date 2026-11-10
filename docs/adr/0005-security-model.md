# ADR-0005: Security model

Status: Accepted (2026-11-06). Owner: CI-ARCH; enforced by CI-PLATFORM.

## Context

Self-hosted runners execute repository code inside the estate cluster. Several
repositories are public (including weather, this repository and
verdify-platform), so anyone can open a pull request. GitHub's guidance is that
self-hosted runners on public repositories are dangerous, because a fork pull
request can run arbitrary code on them.

## Threats and controls

| Threat | Control |
|---|---|
| Fork PR runs code on a runner | Repository setting "require approval for all outside collaborators" on every repository; **and** the job-started guard (a ConfigMap script that a PR cannot change) refuses every fork PR on every profile. Fork contributions are re-pushed to a branch by a maintainer after review. |
| `pull_request_target` / `workflow_run` run fork code with base privileges | Refused by the guard on every profile, and banned by the estate-ci lint. |
| A job from another repository lands on a set | Each set accepts only its own jvallery repository (no org-level sets, D19); the guard checks `GITHUB_REPOSITORY`. |
| PR code reads a write credential | Credentials exist only on trusted profiles (`build`, `ops`), whose guard admits only `push` or `workflow_dispatch` on the default branch of the same repository. PR builds use `build-check` (no credential). Branch protection/rulesets limit who can push to the default branch. |
| Registry poisoning | Per-repository zot principal limited to its own paths; Argo CD deploys only digests committed to the default branch through a reviewed PR, so a pushed tag never deploys by itself. |
| Runner escape or lateral movement | Pod Security baseline; no privileged containers, hostPath, host network or Docker socket; `automountServiceAccountToken: false` and no RBAC; one job per Pod (ephemeral runners); NetworkPolicy allowing only DNS, public 80/443 (private ranges excluded), the apps ingress for the registry and the registry origin. `ops` sets get one extra, named egress rule each. |
| Secret sprawl in GitHub | No repository or organization Actions secrets in the build path, with one accepted exception (below). The only other GitHub-side credential is the per-job `GITHUB_TOKEN`, with least-privilege `permissions:` per workflow. Deploy and registry credentials are Kubernetes Secrets from central KSOPS. |
| Supply chain of workflow code | Only `actions/checkout` and estate-ci reusable workflows/actions, pinned by full SHA. Repository Actions policy enforces it (`allowed_actions: selected`, `sha_pinning_required: true`; estate-ci#111, estate-ci#122). This repository is public, secret-free and protected by a PR-only ruleset with required checks; its self-test and foundation render check run on its own ARC sets. |
| Over-privileged registration App | The registration App needs repository administration (runner registration) and organization self-hosted runners only. It is renamed or replaced by a dedicated App with only those permissions before the fleet Apps are deleted (J-5). |

**Superseding notes (2026-11-09, estate-ci#105).**

- **D-52 (Jason first-hand, 2026-11-06) changes "no Actions secrets" and "Apps
  tokens".** One Actions secret is allowed: `PIN_BOT_PRIVATE_KEY`, with the
  variable `PIN_BOT_CLIENT_ID`, for the GitHub App `vallery-pin-bot` (contents
  and pull_requests write). Only `pin-bump.yml` reads it, and `app-token.py`
  mints a token scoped to the calling repository and revokes it when the step
  exits. The exception is E-2 in [ADR-0011](0011-phase-1-exceptions.md); the
  lint allows exactly that secret name. D-296 removes the other Actions
  secrets after value-blind consumer checks.
- **J-5 is done (J-10).** Runners register with the dedicated App
  `vallery-ci-runners` (Administration RW, Metadata RO); its credential is
  `ci-runners/arc-github-app-jvallery` in central KSOPS. No estate-ci scale set
  uses a fleet App.
- **Break-glass (STD-CI-01, gate-settings unit estate-ci#111).** The estate-ci
  ruleset has no bypass actor (ruleset 26661683, `bypass_actors: []`). The
  bootstrap paragraph below no longer names a standing bypass.

**Bootstrap (R1 for estate-ci itself).** estate-ci's self-test runs on an
`estate-ci-ci` set and the foundation render check on `estate-ci-ci-toolbox`.
Ruleset `main-pr-only-v1` requires `lint / lint` and `foundation / foundation`
(GitHub Actions, app 15368; not up to date) and has no bypass actor
(estate-ci#111, STD-CI-01). If the runners are down, the foundation is repaired
through Argo CD from jvallery/agents (which does not depend on estate-ci
checks). No GitHub-hosted fallback exists.

**Break-glass: a time-boxed ruleset edit, not a bypass.** A bad script in
`deploy/foundation/bin` can fail the required check on the very PR that fixes
it. Then the coordinator executor, on Jason's first-hand decision line:

1. writes a steer line naming the PR, the context to relax and the UTC start;
2. saves `gh api repos/jvallery/estate-ci/rulesets/26661683` to a receipt;
3. PUTs the ruleset with only the failing context removed (never the
   `pull_request`, `deletion` or `non_fast_forward` rules, never a bypass actor);
4. merges that one PR;
5. re-PUTs the saved JSON within 60 minutes of step 3, reads it back equal, and
   writes the closing steer line.

The edit is recorded as an enumerated STD-CI-01 exception use; a standing path
needs its own exception row.

**Advisory repositories (J-2).** Where protection cannot be enforced, the same
guard and credential split apply; only the merge gate is advisory.

## Consequences

- The fork policy and the guard are checked by the parity checklist (ADR-0008)
  for every repository, public ones first.
- New credential types (for example a deploy key for an `ops` set) need their
  own row in the ownership record and an egress rule in the same PR.
