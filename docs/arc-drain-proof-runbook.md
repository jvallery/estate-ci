# ARC through a worker drain: proof runbook (estate-ci#70)

What it proves, in one real worker window under the maintenance Lease:

1. A cordoned worker gets no new runner Pod, and the drain completes with no manual runner delete.
2. A job on that worker finishes (or is evicted and re-run once, STD-CI-03, not counted red).
3. After the drain no host carries more than 50% of the listeners.
4. Controller leader failover: deleting the leader `arc-gha-rs-controller` Pod moves the Lease to the
   standby within 60 s and a job queued meanwhile starts and completes.
5. One listener recreation: a real job on that scale set starts within a recorded time.

## What already holds without a change

- The cordon itself stops new runner Pods: the scheduler never places a Pod on an unschedulable node,
  and runner Pods do not tolerate `node.kubernetes.io/unschedulable`.
- The worker transaction (`scripts/k3s_worker_maintenance.py` in jvallery/proxmox-infrastructure)
  treats EphemeralRunner Pods as batch Pods: the drain first waits up to `--batch-wait` (1800 s) for
  them to finish and never evicts one. Most scale sets have `minRunners: 0`. Gate sets with
  positive minima can keep idle runner Pods, so distinguish those Pods from active jobs in the
  drain receipt. Jobs are capped at 15 minutes (STD-CI-03). The manual runner deletes in the
  2026-11-06/07 evidence predate that transaction.
- A runner Pod a lost node leaves Terminating or `Init:Unknown` for 30 minutes fires
  `EstateCIRunnerStuck` (jvallery/monitoring-stack, alert rule for this issue).

## What this issue changes

- Every scale set's `listenerTemplate` carries a soft hostname spread over all listeners
  (`scripts/render_runners.py` `listener_template()`), so the listeners a drain evicts are recreated
  on the least-loaded hosts instead of piling onto one (2026-11-08: 28 of 59 on node7). Rolling it out
  restarts every listener once (the controller recreates each listener when its
  AutoscalingRunnerSet's listener template changes); jobs queued during the restart start when the
  new listener connects.

## Gates

- Maintenance Lease `kube-system/k3s-planned-maintenance`, one worker, through
  `scripts/k3s-planned-maintenance.py worker-run` with the CNPG-aware switchover first.
- `NS-arc-systems` lock (L11) for the controller and listener deletes, held through the soak.
- Not node8 (Frigate).

## Steps (UTC times into the receipt)

1. Baseline (read-only): listener count per node
   (`kubectl -n arc-systems get pods -l app.kubernetes.io/component=runner-scale-set-listener -o wide`),
   the controller Lease holder (`kubectl -n arc-systems get lease -o wide`), runner Pods per node
   (`kubectl -n ci-runners get pods -o wide`).
2. Queue one real job on a scale set whose runner is likely to land on the target (for example a
   `workflow_dispatch` of a lint workflow), and note its run ID.
3. Run the worker transaction (switchover, cordon, drain, action `none`, uncordon). Right after the
   cordon, read `kubectl -n ci-runners get pods -o wide --field-selector spec.nodeName=<node>` every
   30 s: no Pod created after the cordon time may appear. Keep the drain log.
4. After the drain: the job from step 2 is green (re-run attempt allowed and recorded); listeners per
   node, none above 50%.
5. Leader failover: delete the Pod that holds the controller Lease; record the time the standby takes
   the Lease (read every 5 s) and the start and finish of a job queued during the delete.
6. Listener recreation: delete one listener Pod; record when the new one is Running and when a job on
   that scale set starts.
7. Soak 15 minutes (`EstateCIListenerDown`, `EstateCIJobsStuck`, `EstateCIRunnerStuck` quiet), release
   the lock and the Lease, and write the dated receipt with the measured times into `OWNERSHIP.md`.

## Rollback

- Listener spread: revert the renderer change and re-render; the listeners restart once more.
- The drill deletes only Pods the ARC controller recreates; nothing else changes.
