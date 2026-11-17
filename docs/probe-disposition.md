# Readiness probe disposition: estate-ci workloads

Acceptance slice of jvallery/estate-platform#122 (STD-HA-06 readiness sub-measure: "a serving
workload has a readiness probe or a listed reason"). The pattern is jvallery/network-infra
`docs/state/probe-pdb-disposition.md`. Wording follows the probe-semantics rule that
jvallery/estate-platform#121 publishes under STD-HA-06: readiness gates Service endpoints, and a
justified omission names the container, why a probe gates nothing useful there, and its
compensating signal. "Upstream" alone is never the reason. The full list of 29 and where each row
lives is in jvallery/estate-platform `docs/state/probe-disposition.md`.

Live read 2026-11-17T05:28Z (kube context `vallery`). R = readiness, L = liveness.

| Namespace / workload / container | Replicas | Probes | Disposition (reason) | Compensating signal | Owner |
|---|---|---|---|---|---|
| `arc-systems/arc-gha-rs-controller` / `manager` | 2 | none | Listed reason. This is the ARC scale-set controller, run with `--enable-leader-election` (`--leader-election-id=arc-gha-rs-controller`): only the leader reconciles AutoscalingRunnerSets and listeners. No Service exists in `arc-systems`, and port 8080 is metrics, so readiness would gate no endpoint. | `EstateCIListenerDown` (a listener per scale set, which the controller creates); `KubeDeploymentReplicasMismatch`, `KubePodCrashLooping`. | estate-ci |
