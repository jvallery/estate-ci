# ADR-0007: Runner and job observability

Status: Accepted (2026-11-06). Owner: CI-ARCH; rules and dashboards in
jvallery/monitoring-stack (the estate observability owner).

## Decision

**Signals.**

- ARC listener metrics (enabled per scale set through the chart's listener
  metrics settings): assigned, running, registered and idle runners; job startup
  and execution duration histograms; per scale set and repository.
- Kubernetes: `ci-runners` quota usage, Pending runner Pods and their reasons,
  OOMKilled and evicted runner containers, ephemeral-storage evictions.
- Registry: push failures and the capacity signal (owned by storage-infra).
- No GitHub API poller. Job outcome history stays in GitHub; the cluster
  metrics answer "is CI capacity healthy".

**Alerts** (monitoring-stack, with runbooks in estate-ci):

| Alert | Condition (initial) |
|---|---|
| `EstateCIListenerDown` | a listener Pod not ready for 10 min |
| `EstateCIJobsStuck` | assigned jobs > 0 and running = 0 for one set for 15 min |
| `EstateCIJobStartupSlow` | p90 startup above 5 min for 30 min |
| `EstateCIQuotaSaturated` | `ci-runners` requests above 90% of quota for 30 min |
| `EstateCIRunnerOOM` | any runner or sidecar OOMKilled (info, with the set name) |
| `EstateCIRunnerUnschedulable` | runner Pod Pending (Unschedulable) for 10 min |

**Dashboard.** One generated dashboard ("Estate CI") in the monitoring-stack
taxonomy, built by its dashboard generator: queue depth, startup and run time by
repository, quota use, OOMs, and registry push errors.

**Retirement.** The fleet CI alerts, dashboards and probes are removed by the
decommission epic only after the replacement alerts are live.
