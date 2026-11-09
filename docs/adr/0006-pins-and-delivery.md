# ADR-0006: Digest pins, the forge wrapper and Argo CD delivery

Status: Accepted (2026-11-06). Owner: CI-ARCH; built by CI-PLATFORM.

## Decision

- **Argo CD delivery is unchanged.** CI never applies to the cluster. Merged
  digest pins in Git are the deploy; Argo CD syncs them (automatically, or
  through the coordinator's exact sync for manual and SHA-pinned Applications).
  CI never edits an Argo `targetRevision`; SHA-pinned parents keep moving only
  through the consolidated pin PR (L4).
- **Pin flow.** `build.yml` (on `build`) outputs digests; a separate `pin` job on
  the `ci` profile runs `ci/pin-bump.sh`, which edits the pin files with plain
  text/YAML tools (kustomize `images:`, inline `image@sha256:` references) or
  runs the repository's registered renderer for generated pin files (never a
  hand edit of a generated file), commits on a branch and calls the forge
  wrapper.
- **Forge wrapper.** estate-ci ships one script, `forge`, the only place that
  talks to the forge API: `forge pr-create`, `forge pr-merge-auto`,
  `forge dispatch <workflow> <ref>`, `forge status`. It reads `FORGE_URL`,
  `FORGE_REPO` and `FORGE_TOKEN` (today `GITHUB_TOKEN`). Phase 2 swaps its
  implementation and nothing else.
- **Checks on pin PRs.** A PR opened with `GITHUB_TOKEN` does not trigger
  workflows, so the pin job dispatches the repository's `ci` workflow on the pin
  branch. The required contexts therefore appear on the pin head.
  *Superseded 2026-11-06 by D-52 (note 2026-11-09, estate-ci#105):* the pin PR
  is opened with a repo-scoped installation token of the GitHub App
  `vallery-pin-bot` (`app-token.py`, ADR-0011 E-2), so `pull_request`
  workflows run and the required checks attach. The dispatch is only the
  fallback when a caller passes no pin-bot key, and a PR from that path stays
  BLOCKED on required checks.
- **One pin writer.** A repository's native pin writer (Sensor, build row, or
  bespoke direct-commit template) is disabled in the same window that the GHA
  pin job is enabled. Never two writers.
- **Direct commits.** Repositories whose contract today is a direct pin commit
  to the default branch move to pin PRs with auto-merge where the plan allows; a
  direct commit stays only where a ruleset bypass already exists and the owner
  lane documents why. *Note 2026-11-09 (STD-CI-01, estate-ci#105):* a standing
  bypass is not allowed; a direct pin commit needs an enumerated STD-CI-01
  exception row, otherwise pins land by PR.

## Consequences

- Pin PRs from CI appear in the merge flow like any other PR; lanes merge them
  under the usual compare-check.
- Pins of manual or SHA-pinned Applications (for example cortex, kepler, the
  Home Assistant ops app) still need a coordinator exact sync; the migration
  issue says which.
