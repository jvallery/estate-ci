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
   but never below that p90; classes with fewer than 5 Pods keep their value. The values live in `runners.yaml` (`*_resources`). With
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
4. **Spread all runners together.** Every runner Pod carries
   `estate-ci.vallery.net/runner: "true"`, and one soft hostname
   `topologySpreadConstraint` (maxSkew 1, ScheduleAnyway,
   `nodeTaintsPolicy: Honor`) counts all of them, whatever the set or profile.
   It stays soft: a hard spread strands runners (agents#5560); requests and
   slots are the hard limits.
5. **One per-node cap, node8 the only exception.** Each runner Pod requests one
   `agentfleet.vallery.net/ci-runner-slot`. vm-k3s-node4..7 advertise the same
   value (20); vm-k3s-node8 advertises 4, behind the hot guard (D-67), and
   prefers-away affinity keeps it the overflow node. Control-plane nodes
   advertise 0. No other node carries a runner-specific label, taint or cap.
   The slot is a backstop: with measured requests, CPU normally binds first.
6. **The quota is the overall envelope.** `deploy/foundation/quota.yaml`
   bounds requests across every set. A Pod the quota refuses is retried by the
   ARC controller; its job waits in GitHub. `maxRunners` per set is a fairness
   cap between repos, not a capacity statement: the sum of `maxRunners` may
   exceed what the workers hold at once.
7. **The quota is also the per-worker CPU budget** (estate-ci#69, 2026-11-09).
   `runners.yaml` `capacity` records a reserve (7 CPU of 47, the 85% target
   below) and each worker's non-runner requests; `render_runners.py` fails when
   the runner quota's `requests.cpu` is above the sum over workers of
   min(allocatable - reserve - non-runner, slots x largest runner Pod request).
   The slot count cannot be that budget: 20 slots x 2.8 CPU is 56 CPU against
   13-22 CPU of headroom per worker, and STD-CI-10 fixes the slot values.
   Tests: `tests/runner_budget/run.py`.

## Capacity (2026-11-07 sample)

- Worker allocatable 43 CPU each. Non-runner requests: node4 27, node5 11
  (just back from maintenance), node6 23.5, node7 21.5, node8 33 CPU (Frigate).
  Runner room by requests is therefore about 16-22 CPU per node4..7 worker and
  about 10 on node8, about 80-90 CPU in all.
- After this change the mean runner Pod requests about 1.35 CPU (range 0.2 to
  4), so the workers hold about 60 runners at once by CPU; the slot caps
  (4 x 16 + 8 = 72 then; 4 x 20 + 4 = 84 from STD-CI-10) stay above that and
  only bind if requests drift low.
- 2026-11-09 (estate-ci#69): allocatable 47 CPU per worker; non-runner requests
  node4 24, node5 18, node6 22.5, node7 27, node8 27.5 CPU; runner budget
  79.7 CPU, quota requests.cpu 79.
- Target: workers under about 85% of allocatable CPU in steady state.

## Re-sizing

Run when a class changes shape (new test suite, sharding) or every few weeks.
Use the last 6 h (or more) of data that includes busy periods.

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
