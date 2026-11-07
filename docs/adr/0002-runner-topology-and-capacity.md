# ADR-0002: Runner topology, profiles, quotas and capacity model

Status: Accepted (2026-11-06). Owner: CI-ARCH; built by CI-PLATFORM.
Capacity points 2-4 and the node8 slot value are superseded by
[ADR-0010](0010-runner-balancing.md) (D-87).

## Context

- The existing ARC controller (gha-runner-scale-set controller, cluster-wide)
  already serves 19 scale sets: 18 in `github-runners`, rendered from the fleet
  registry by a 2,749-line fleet renderer with a fleet toolbox image, plus one in
  `frigate`. Those are fleet dependencies and retire with their repositories.
- `jvallery` is a user account, so its scale sets register per repository.
  VerdifyConsultancy is an organization and could use org-level sets, but on the
  Free plan its only runner group is open to every private repository.
- The GPU workers are memory-bound: on 2026-11-06 nodes 4, 5 and 7 had 96-98% of
  allocatable memory requested and 90-98% of CPU. Native CI holds two quotas
  (about 76 CPU / 81 GiB of requests together) and used about 56 CPU / 66 GiB of
  requests at the sample.

## Decision

**Namespace.** All new runners live in `ci-runners` (Pod Security `enforce:
baseline`, `warn/audit: restricted`), rendered from `runners.yaml` by
`scripts/render_runners.py` into one Argo CD Application per scale set. The
`github-runners` sets are not extended; each retires when its repository cuts
over.

**jvallery only (D19).** ARC scale sets register only for jvallery repositories.
A VerdifyConsultancy repository that needs CI is transferred to jvallery first
(REPO-MOVE) and gets its repo-level sets after the transfer. There are no
VerdifyConsultancy or org-level scale sets, and no VerdifyConsultancy
registration Secret is needed in `ci-runners`. The transitional `verdify-ci` and memoryos org-level
sets (estate-ci#12) are removed by CI-PLATFORM once the REPO-MOVE transfers
are done. New work does not target them: on the Free plan the org's only
runner group is open to every private repository in the organization.

**Scale-set naming.** `<repo>-<profile>` (the name is the `runs-on` label).

**Profiles.**

| Profile | Pod | Trust | Default max |
|---|---|---|---|
| `ci` | runner (+ optional `job` sidecar running the repo's CI image) | untrusted (PR code) | 3 per repo |
| `pg` | runner + Postgres (pgvector) sidecar on emptyDir (+ job) | untrusted | 1 per repo, 2 estate-wide |
| `build-check` | runner + Kaniko sidecar, no registry write credential (`--no-push`) | untrusted | 1 per repo |
| `build` | runner + Kaniko sidecar + the repo's registry writer | trusted: default-branch push only (ADR-0005) | 1 per repo |
| `ops` | runner + a named deploy credential and a dedicated egress policy (for example HAOS deploy, Cloudflare Worker deploy) | trusted: default-branch push or dispatch only | 1 per repo |

Profiles are classes, not repos: a repository only gets the profiles its
workflows use. Image mirrors whose Dockerfile is only `FROM <upstream>@<digest>`
do not need Kaniko: they use `crane copy` on a `build` set (ADR-0003), which
removes the heaviest builds (vLLM and GPUStack mirrors) from the build budget.

**Capacity model.**

1. **One envelope.** One ResourceQuota on `ci-runners` bounds every set. It grows
   only by what a cutover removes from `agent-fleet-ci-budget` /
   `agent-fleet-ci-foreign-budget` and from the retiring `github-runners`
   quotas, in the same change set. Target after all cutovers: about 40 CPU /
   64 GiB of requests, below native today because the controller, EventBus,
   sensors and per-run PVC churn disappear.
2. **Requests at typical use, limits at peak.** Runner containers request 250m /
   512 MiB; job sidecars and Kaniko request the measured p50 working set of the
   repo's native runs and limit at about 1.5 × p95. Kaniko memory limits are
   set per image (12 GiB default, more only with measured evidence).
3. **Never preempting.** Runner Pods use `ci-runner` (value 0, preemptionPolicy
   Never): they never evict anything and wait for capacity, and only tiers above
   the default (estate-app and up) can preempt them. A negative class made them
   the first victims of every default-priority pod, killing jobs mid-run
   (2026-11-06). Pods tolerate no taints (node8
   stays Frigate-only; control-plane nodes stay cordoned).
4. **Soft spread only.** No hard hostname anti-affinity (it strands runners);
   a preferred spread across workers is enough.
5. **Ephemeral storage, no PVCs.** Work directories and build contexts use
   emptyDir with ephemeral-storage requests and limits; `persistentvolumeclaims:
   0` in the quota. That removes native CI's 1,300-PVC Longhorn churn.
6. **Concurrency caps.** `maxRunners` per set plus the quota. A queued job waits
   in GitHub, not in the cluster. Heavy suites shard (each shard under 15
   minutes) and share the estate-wide `pg` cap.
7. **Scale from zero.** `minRunners: 0` everywhere; listener Pods (about 10m CPU
   each) in `arc-systems` are the only idle cost.

## Consequences

- Capacity is planned per cutover in the PR that adds a set: the PR states the
  quota increase and the matching native decrease.
- ARC (the controller and the chart) becomes estate platform. Its upgrades are
  owned by jvallery/agents platform (the controller) and estate-ci (scale-set
  chart version in `runners.yaml`).
- If the workers stay above 95% of memory requests, cutovers still proceed
  because they move requests rather than add them; new parallelism waits for
  capacity work (VM RAM, rightsizing) in the hosts lane.

## Addendum: node8 overflow (D-67, 2026-11-07)

Jason approved this first-hand: every estate-ci runner set may use vm-k3s-node8 (Frigate's GPU/NVR node) as overflow, with the same runner shape as everywhere else, and runners always lose to Frigate.

- **Placement** (renderer, every set): the runners tolerate `dedicated=frigate:NoSchedule` and carry a soft node affinity (weight 100) away from vm-k3s-node8, so node4-7 fill first. The scheduler balances this against the soft hostname spread, so node8 can still take a share before node4-7 are full. The slot cap bounds that share.
- **Priority**: PriorityClass `ci-preemptible` (-1000, preemptionPolicy Never). Frigate (estate-default, 0) and every app can preempt a runner, and a runner never preempts anything. Jason accepted that running jobs may be killed (it was the reason for `ci-runner` 0 on 2026-11-06).
- **Per-node cap**: each runner Pod requests and limits one `agentfleet.vallery.net/ci-runner-slot`. The jvallery/agents arc-validation-pool reconciler advertises it from ConfigMap `github-runner-ops/ci-runner-slots`: node4-7 64 (3-day per-node peak 38), node8 16 for the 24 h soak, after which COORD decides on 24. This resource is independent of `arc-validation-slot`.
- **Hot guard**: CronJob `ci-runner-hot-guard` (`deploy/foundation/hot-guard.yaml`, `bin/hot-guard.py`, thresholds in `hot-guard-config.json`) runs every minute. It trips on any of: node8 CPU non-idle >0.95 for 2 min; node8 memory >0.85 for 2 min; Frigate skipped/camera fps >0.10 for 2 min; decode deficit (camera-process)/camera >0.20 for 2 min; a Frigate container started <5 min ago. On a trip it writes `ci-runners/ci-runner-slot-hold` (the reconciler then advertises 0 on node8 within about a minute) and evicts node8 runner Pods newest-first, 2 per run, until the trip clears. It reopens after 15 clean minutes. A missing signal or an unreachable Prometheus holds node8 without evicting. Alerts are `EstateCINode8GuardTripped` and `EstateCINode8GuardFailing` (jvallery/monitoring-stack).
- **Two-tier guard (COORD 2026-11-07T03:49Z)**: the first live CPU-only trip (03:45Z, node8 at 0.866 with Frigate clean) deleted busy runners and killed in-flight jobs. The guard is now tiered. HARD signals (Frigate skipped ratio >0.03 or decode deficit >0.06, each as the minimum over 4 min; a Frigate container started <5 min ago; node8 memory >0.85 for 2 min) close node8 (0 slots) on the first run, and evict newest-first only when the next run confirms the trip (COORD 2026-11-07T04:08Z: delete runners only on a confirmed Frigate signal; an operator's forced trip counts as confirmed). The SOFT signal is node8 CPU, minimum over 4 min >0.85, with Frigate clean. It writes `cap=4 ...` and the reconciler (jvallery/agents, "cap=N" soft hold) advertises min(desired, 4). Nothing is evicted, and the cap drops once node8 CPU, maximum over 10 min, is <0.75. A reconciler that predates "cap=N" reads the value as a plain hold (0 slots), so the soft tier fails closed. The Frigate thresholds sit above both measured 1 h baselines (skip 0.025 and 0.00002; deficit 0.053 and -0.001).
- **Tests**: `python3 tests/hot_guard/run.py` (in self-test). Dry run against the live cluster, where every write is dryRun=All: create a Job from the CronJob with env `GUARD_MODE=dry-run`. Forced trip: the same Job with `GUARD_FORCE_TRIP=test`, run in enforce mode. It holds node8 and evicts node8 runners. Remove the hold entry (`kubectl -n ci-runners patch cm ci-runner-slot-hold --type merge -p '{"data":{"vm-k3s-node8":null}}'`) or wait 15 clean minutes.
