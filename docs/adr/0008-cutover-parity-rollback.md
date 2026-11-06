# ADR-0008: Cutover, parity, ruleset switch, rollback and decommission order

Status: Accepted (2026-11-06). Owner: CI-ARCH. The executable recipe is
[`docs/MIGRATION-RUNBOOK.md`](../MIGRATION-RUNBOOK.md); this ADR sets the gates.

## Per-repository cutover

1. **Prepare.** Sets in `runners.yaml` (quota moved, not added); `ci.yml`
   (and `build.yml` with pushes off) in the repository; registry principal if it
   builds. Required checks unchanged.
2. **Parallel run.** Actions runs non-required next to native CI.
3. **Builds and pins.** Native build/pin rows disabled (agents PR) in the same
   window as the GHA pin job is enabled; the first GHA-built digest deploys.
4. **Required checks.** One ruleset or protection write, before/after JSON kept.
5. **Remove native wiring.** Catalog row, Sensor, generated
   `.agent-fleet/ci.yaml`, webhook and legacy `github-runners` set; the native
   quota is lowered by the repository's share.
6. **Verify and close** (L9, independent verifier).

## Parity definition

A repository is at parity when all of these hold:

| Gate | Tier A: merge-gated or image-building | Tier B: static checks only |
|---|---|---|
| Runs compared (fast path, D19) | 5 PRs where every Actions verdict agrees with native; **no minimum duration** | 3 runs including one default-branch push; no minimum duration |
| Verdicts | every Actions verdict matches native, or each difference is explained and fixed | same |
| Coverage | every native step has a named Actions job (or a documented, accepted removal) | same |
| Wall time | p90 no worse than native's p90 (queue + run) | not gated |
| Build | a GHA-built digest is byte-identical in function: it deploys Synced/Healthy and its live probe passes (dev/canary first where it exists) | n/a |
| Security | fork policy set; guard refusals tested once (fork PR or wrong-event job refused) | same |
| R1/R2 | estate-ci lint green on all workflows | same |

## Ruleset switch

- Contexts become `<workflow job id> / <name>` check runs from GitHub Actions
  (integration 15368). A summary job carries the context when the suite shards.
- "Require branches to be up to date" stays off (L1).
- Advisory repositories (J-2) skip the write and say so in the issue.
- jvallery/agents switches last (J-6), after its own Actions are re-enabled and
  at parity.

## Rollback

| Step | Undo |
|---|---|
| 1-2 | Delete the workflow files; revert the `runners.yaml` PR. |
| 3 | Re-enable the native catalog row; set pushes off. One pin writer at all times. |
| 4 | Re-PUT the saved ruleset/protection JSON. |
| 5 | Not undone once native for that repository is gone; the gate before step 5 is the 7-day soak after step 4 with no rollback. |

## Program order

| Wave | Repositories / work |
|---|---|
| W0 | Foundation (then **jvallery/agents first**, D19: onto ARC as soon as its estate-ci delivery lands, to get off the ~17-minute native rounds; J-6 ruleset switch after fast-path parity): ci-runners, profiles, guard, toolbox image, mounted registry credential, forge wrapper, lint, observability. Pilot: weather. |
| W1 | research-vault (starved on native today), verdify-platform (timed with the transfer) |
| W2 | homeassistant, kepler, weather (merge-gated); build moves for storage-infra, cortex-ai-compute, monitoring-stack, network-infra |
| W3 | docs, options-tracker, sentinel, verdify-www, sunshine_club, then the static-only repositories (Tier B) |
| W4 | Estate jobs re-homed (they can run any time after W0), then decommission. (jvallery/agents moved to W0 by D19.) |

**Transfers.** ARC serves jvallery repositories only (D19), so a VerdifyConsultancy repository that needs CI transfers before its cutover. A repository that moves owner (REPO-MOVE) cuts over either fully
before or fully after the transfer, never across it: the transfer changes the
repository URL, the Argo repoURL, webhook and App installation, and the
`runs-on` sets. Preferred: transfer first, then cut over on jvallery sets.

## Decommission sequencing (with the decommission epic)

1. Estate jobs off native CI (D1) and every repository past step 5.
2. Fleet references removed from probes, routes and docs (D2).
3. Native CI scaled to zero through Git (D3), 10-minute soak per component.
4. Applications removed by exact delete; mixed roots split (D4).
5. Coordinator delete-sets: namespaces, PVCs, buckets (D5); fleet GitHub Apps
   last, after the registration App is replaced (J-5).
6. Fleet code removed from jvallery/agents (D6).

The quota retired at each step moves to `ci-runners` only as far as measured
demand needs; the rest returns to the cluster.
