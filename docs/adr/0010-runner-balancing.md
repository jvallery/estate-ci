# ADR-0010: Runner balancing across the workers

Status: Accepted (2026-11-07, D-87). Supersedes ADR-0002 capacity-model points
2 (request sizes) and 4 (spread selector), and the node8 slot value in the
ADR-0002 D-67 addendum.

## Context

On 2026-11-07 about 95 runner Pods requested 100m-500m CPU each but used up to
about 2 CPU each under load. The scheduler places Pods by requests, so it
stacked 20-27 runners per worker and vm-k3s-node4..7 ran at 101-111% of
allocatable CPU. Production Pods with small requests lost: a database liveness
probe timed out, a planner missed its 120 s budget, and a registry signal ran
out of its time budget. CFS gives each container CPU in proportion to its
request, so a node whose summed requests understate its real load starves the
smallest requesters first.

Jason's directive (D-87): balance the runners for the long term; vm-k3s-node8
(Frigate) is the only exception, with a reduced cap; the other workers are all
the same.

## Decision

1. **Requests are measured, per runner class.** Each container of each scale
   set (`runner`, `job`, `kaniko`, `postgres`) requests about the p90 of its own
   CPU use (p90 of all 1-minute samples of its running Pods over the last
   6 h, so idle stretches inside a job count; p95 where p95 exceeds p90 by at
   least 20% and 200m, D-103 B), and the p90 of its peak working
   set, rounded up to 50m / 128 MiB. A sizing pass moves requests up or down
   but never below that p90; classes with fewer than 5 Pods keep their value. The values live in `runners.yaml` (`*_resources`).
   **Amended by D-488 (Jason 2026-11-09: no more cores; CPU use stays low on the
   existing ones).** CPU requests now follow active use, not p90: job, kaniko
   and postgres containers, and a runner container that runs the job steps
   itself, request the p50 of their active CPU (1-minute samples with rate >
   0.05), floor 250m; a runner container beside a job sidecar requests its own
   active p75, floor 100m; rounded up to 50m; classes with fewer than 20 active
   samples keep their value. Limits and memory requests are unchanged. Runners
   are the elastic class: a lower request lets more of them schedule on idle
   CPU, and under contention CFS shares follow requests, so runners yield to
   platform Pods instead of holding CPU they mostly do not use. With
   realistic requests the scheduler stops placing runners on a worker once its
   CPU is spoken for, and the default `LeastAllocated` scoring spreads them.
2. **Limits protect neighbours.** No container may set a CPU limit above 8
   (`MAX_CPU_LIMIT` in the renderer, which refuses a higher value or a request
   above its limit). 8 CPU is 1/5 of a 43-CPU worker and above the p99 of every
   measured class. Memory limits stay per class (ADR-0003 for Kaniko).
3. **Runners always lose to production; infra schedules first.** PriorityClass
   `ci-preemptible` (-1000, preemptionPolicy Never, D-67) for app repos and
   `ci-preemptible-infra` (-900, Never) for the repos in `runners.yaml`
   `defaults.infra_repos`. Every production Pod (estate-batch -100 and above)
   can preempt either; runners never preempt. Pending Pods are ordered by
   priority, so freed CPU goes to infra runners before app runners, however
   many jobs an app repo has queued (fair share, 2026-11-07).
   **Incident-critical sets (D-528, estate-ci#179, 2026-11-12).** The scale
   sets named in `runners.yaml` `defaults.incident_sets` use PriorityClass
   `ci-incident` (-800, `preemptionPolicy: PreemptLowerPriority`). When every
   `ci-runner-slot` is taken, the scheduler evicts a lower-priority runner Pod
   (-900 or -1000) to place theirs, so a lane restoring a production service
   gets a runner within about 2 minutes. The evicted job fails and is re-run
   (STD-CI-03 treats eviction the same way). Production tiers (-100 and above)
   still preempt `ci-incident`, it counts against the same runner quota
   (`estate-ci-runner-pods`), and slot counts and the hot guard are unchanged,
   so node CPU safety is as before. When that quota itself is exhausted the Pod
   is refused at admission and preemption cannot help; that case is not covered.
   The list holds explicit scale-set names and the renderer refuses an unknown
   one. COORD designates a set (or Jason directly) by a PR to `runners.yaml`,
   delivered through the `estate-ci-runners` pin, and removes it the same way
   once the set no longer needs incident priority. The first set is
   research-vault (`-build`, `-ci`, `-pg`), the sets that waited 7-20 minutes in
   the 2026-11-10 Research Vault incident.
   **Incident-only use (D-610 a, 2026-11-13).** A set is named in
   `incident_sets` only for a declared incident with an end time: one PR adds
   it when the incident is declared and one PR removes it at the end time. It
   is never a standing tier. `research-vault-ci` and `-pg` returned to
   `ci-preemptible-infra` after their 25 live runner Pods preempted
   `agents-ci` runners (pod condition `PreemptionByScheduler`,
   2026-11-13T01:01Z). The `ci-incident` PriorityClass itself stays defined.
4. **Spread all runners together.** Every runner Pod carries
   `estate-ci.vallery.net/runner: "true"`, and one soft hostname
   `topologySpreadConstraint` (maxSkew 1, ScheduleAnyway,
   `nodeTaintsPolicy: Honor`) counts all of them, whatever the set or profile.
   It stays soft: a hard spread strands runners (agents#5560); requests and
   slots are the hard limits.
5. **One per-node cap, node8 the only exception.** Each runner Pod requests one
   `agentfleet.vallery.net/ci-runner-slot`. vm-k3s-node4..7 advertise the same
   value (16 at D-87; 20 now, see the note below); vm-k3s-node8 advertises 8
   (4 now), behind the hot guard (D-67), and
   prefers-away affinity keeps it the overflow node. Control-plane nodes
   advertise 0. No other node carries a runner-specific label, taint or cap.
   The slot is a backstop: with measured requests, CPU normally binds first.

   *Superseding note (2026-11-09, estate-ci#105).* D-141 (Jason first-hand,
   2026-11-07T21:06:45Z, "bump runner slots on all the worker nodes") raised
   vm-k3s-node4..7 to 20. D-168 corrected vm-k3s-node8 back to its live 4
   (Frigate node). Live `ci-runner-slot` allocatable read 2026-11-09:
   node4 20, node5 20, node6 20, node7 20, node8 4, node1-3 0 (84 in all).
   D-168 was first decided (c) (keep as is) and the 2026-11-09 sweep then
   accepted its recommendation (a): cap vm-k3s-node5 (the GPU node that went
   NotReady on 2026-11-07, cortex-ai-compute#549) at 6, keeping node4/6/7 at 20
   and node8 at 4, with a follow-up on runner CPU limits. That cap is delivered
   in ConfigMap `github-runner-ops/ci-runner-slots` (jvallery/agents, moving here
   under estate-ci#76) and is not live yet; update these values in the same PR
   that delivers it.
6. **The quota is the overall envelope.** `deploy/foundation/quota.yaml`
   bounds requests across every set. The ResourceQuota admission plugin
   rejects a runner Pod create over it (403 "exceeded quota"); ARC 0.14.2
   retries the create every 30 s and re-creates an EphemeralRunner that is
   older than 10 minutes, and its job waits in GitHub meanwhile. `maxRunners` per set is a fairness
   cap between repos, not a capacity statement: the sum of `maxRunners` may
   exceed what the workers hold at once.
7. **The quota is also the per-worker CPU budget** (estate-ci#69, 2026-11-09).
   `runners.yaml` `capacity` records a reserve (2.35 CPU of 47: runners may fill
   a worker to 95% of allocatable, D-486) and each worker's non-runner requests; `render_runners.py` fails when
   the runner quota's `requests.cpu` is above the sum over workers of
   min(allocatable - reserve - non-runner, slots x largest runner Pod request).
   The slot count cannot be that budget: 20 slots x 2.8 CPU is 56 CPU against
   13-22 CPU of headroom per worker, and STD-CI-10 fixes the slot values.
   **The reserve is aggregate, not per node.** A namespace quota does not stop
   the scheduler from filling one worker's free requests with runners, so a
   single worker can still reach zero reserve while the total stays inside the
   budget. `non_runner_cpu` is a recorded snapshot with no drift check;
   `EstateCIWorkerCPUSaturated` (monitoring-stack) is the live signal.
   STD-CI-10's fixed slot values rule out a static per-node slot budget, so
   the per-node reserve is enforced at runtime (estate-ci#69, 2026-11-10,
   after node4/5/7 reached 1.02 of allocatable within 4 h of #160): the hot
   guard's reserve tier (`hot-guard-config.json` `reserve`) writes
   `cap=10` into `ci-runner-slot-hold` for any of node4-7 whose CPU busy /
   allocatable stays above 0.92 (D-502 (b) trip-threshold half, Jason 2026-11-10; was 0.85. A cap of 16 at 0.92 never limited node6 and its kubelet stalled, estate-ci#177) for 2 minutes, so the slot advertiser offers
   min(desired, 10) there and new runners go to other workers or queue in
   GitHub. Nothing is evicted; the cap drops once the 30-minute maximum is
   below 0.75 (2026-11-10: a 10-minute window below 0.85 cleared node6's cap
   after one quiet window and its kubelet stalled when the runners refilled).
   An empty reserve result, or node CPU sample coverage below 0.95 over 30
   minutes, counts as hot, because a saturated node stalls its own telemetry.
   A failed reserve query writes nothing, so a Prometheus outage does not
   stop CI on node4-7.
   Lowering the quota below the 48 h peak of runner requests (124.9 CPU) means
   bursts queue in GitHub; at 98 no period can have every set at its
   `maxRunners`, so estate-ci#69's 7-day acceptance window needs that clause
   amended in the same decision. If node5's slots drop to 6, the budget falls
   to about 88.4 CPU and the quota must go to 88 (render fails above it).
   Tests: `tests/runner_budget/run.py`.

## Capacity (2026-11-07 sample)

- Worker allocatable 43 CPU each. Non-runner requests: node4 27, node5 11
  (just back from maintenance), node6 23.5, node7 21.5, node8 33 CPU (Frigate).
  Runner room by requests is therefore about 16-22 CPU per node4..7 worker and
  about 10 on node8, about 80-90 CPU in all.
- After this change the mean runner Pod requests about 1.35 CPU (range 0.2 to
  4), so the workers hold about 60 runners at once by CPU; the slot caps
  (4 x 16 + 8 = 72 then; 4 x 20 + 4 = 84 since D-141 and D-168, fixed by STD-CI-10) stay above that and
  only bind if requests drift low.
- 2026-11-09 (estate-ci#69): allocatable 47 CPU per worker; non-runner requests
  node4 24, node5 18, node6 22.5, node7 27, node8 27.5 CPU; runner budget
  79.7 CPU, quota requests.cpu 79 at reserve 7 (85%).
- 2026-11-09 D-486 basis: 95% of allocatable (reserve 2.35 CPU per worker);
  non-runner requests 117.3 measured 2026-11-09T23:5xZ (recorded rounded up:
  node4 24, node5 18, node6 22.5, node7 27, node8 27.5); 110 in the decision
  used a stale 112. Runner budget 98.3 CPU (node8 held to 4 slots x 2.8 =
  11.2), quota requests.cpu 98. The budget rises when storage-infra#1666 frees
  Longhorn instance-manager requests (~15-18 CPU); re-measure non_runner_cpu
  and re-render the quota then.
- 2026-11-10 D-488 sizing (sample 2026-11-06T12:20Z..11-10T00:35Z, the whole
  `ci-runners` history, about 22,000 runner Pods): weighted mean runner Pod
  request 1.09 -> 0.87 CPU; sum at every set's `maxRunners` 308 -> 235 CPU.
  At the 48 h peak-request minute (68 Pods, 104.8 CPU requested) the same mix
  now requests 79.4 CPU, so quota requests.cpu 98 (unchanged) holds the slot
  cap (84 slot Pods + 6 gate Pods) rather than about 63 Pods of that mix.
  Largest Pod is verdify-platform-ci-dashboard at 3.9 CPU (job p50 3.72), so
  node8's ceiling is 4 x 3.9 = 15.6 and the render budget 102.7; the quota is
  not raised with it. Workers at that time: node CPU use p50 9.5-21 CPU, p95
  36-42 CPU, peak 47-51 of 47 allocatable, runners using more than they
  request at each node's peak (bursting on limits).
- Target: workers under about 85% of allocatable CPU in steady state.

## Re-sizing

Run when a class changes shape (new test suite, sharding) or every few weeks.
Use the last 6 h (or more) of data that includes busy periods. Since D-488 the
CPU percentile is p50 (work containers) or p75 (runner beside a job), pooled
across every active 1-minute sample of the set, not p90 (point 1).

```promql
# per set and container: p90 of active CPU (5m rate, 1m step); pod names are
# <set>-<hash>-runner-<id>
quantile_over_time(0.9,
  (sum by (pod, container) (rate(container_cpu_usage_seconds_total{
     namespace="ci-runners", container!="", container!="POD"}[5m])) > 0.05)[24h:1m])
# peak working set
max_over_time(container_memory_working_set_bytes{namespace="ci-runners", container!="", container!="POD"}[24h])
```

A suite that shards across its cgroup CPU limit (CI-SHARD-ALL: storage-infra,
research-vault) bursts to the limit while its tests run; its request still
follows the 6 h sample p90, and the burst comes from idle CPU (2026-11-07:
requesting 6 CPU made CPU requests the bottleneck and left runners pending).

Group the per-Pod results by set (strip `-<hash>-runner-<id>`) and take the
p90 across Pods. The first sizing (this ADR) used 14 h of data
(2026-11-06 12:19Z to 2026-11-07 02:15Z, about 3,900 runner Pods), the full history
of the `ci-runners` namespace at the time. Classes with no data kept their
previous values.

## Observability

jvallery/monitoring-stack records per-node runner count, runner CPU requests
and CPU saturation (`estate_ci:runner_pods:count_by_node`,
`estate_ci:runner_cpu_requests:sum_by_node`,
`estate_ci:node_cpu_usage:ratio_allocatable`) and alerts when a runner-eligible
worker stays above 95% of allocatable CPU with runners on it
(`EstateCIWorkerCPUSaturated`).

## Where the slot reconciler lives

ConfigMap `github-runner-ops/ci-runner-slots` and the reconciler that
advertises `ci-runner-slot` are in jvallery/agents
(`platform/kubernetes/github-runners/foundation/validation-pool.yaml`), shared
with the legacy `arc-validation-slot`. They move to this repository
(`deploy/foundation/`) when the legacy github-runners sets retire, so no new
estate-ci behaviour depends on jvallery/agents (tracked in an estate-ci issue).

## Consequences

- Fewer runners run at once than before (about 60 instead of 95), but each runs
  at its measured speed instead of contending at 105-110% CPU; queue time moves
  to GitHub, where the existing EstateCI queue alerts see it.
- A job that needs more CPU than its class requests still bursts to its limit
  when the node has idle CPU.
- Raising a class's request is the knob for a slow class; raising `maxRunners`
  alone does not add capacity.
