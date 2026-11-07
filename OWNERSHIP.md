# OWNERSHIP: jvallery/estate-ci

Owner repo for the estate's standard CI/CD substrate. Registered in
jvallery/agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`.

| Item | Where | Health / how to check | Depends on |
|---|---|---|---|
| Reusable workflows `checks.yml`, `image-build.yml`, `pin-bump.yml` | `.github/workflows/` | `self-test` workflow green on `main` | GitHub Actions |
| Runner scripts (`image-build.sh`, `pin-bump.sh`, `forge-api.sh`, `sidecar-run.sh`, `wait-postgres.sh`, `check-runner-labels.sh`, guard, agent) | `deploy/foundation/bin/` → ConfigMap `estate-ci-scripts` at `/opt/estate-ci` | `self-test` | — |
| Namespace `ci-runners` (PSA enforce baseline) | `deploy/foundation/namespace.yaml` | `kubectl get ns ci-runners --show-labels` | Argo `estate-ci-foundation` |
| ResourceQuotas `estate-ci-runners` (object caps), `estate-ci-runner-pods` (runner envelope), `estate-ci-ops` (hot guard), LimitRange `estate-ci-defaults` | `deploy/foundation/quota.yaml` | `kubectl -n ci-runners describe quota` | — |
| NetworkPolicies `default-deny`, `runner-egress` | `deploy/foundation/networkpolicies.yaml` | runner jobs reach GitHub and zot | EDGE-WRITE.lock to sync |
| node8 hot guard (D-67): CronJob `ci-runner-hot-guard`, ServiceAccount, Roles/RoleBindings `ci-runner-hot-guard` and `ci-runner-slot-hold-reader` (the agents reconciler reads the hold), NetworkPolicy `hot-guard-egress`, ConfigMaps `ci-runner-hot-guard`, `ci-runner-hot-guard-config` and the guard-created `ci-runner-slot-hold` | `deploy/foundation/hot-guard.yaml`, `bin/hot-guard.py`, `hot-guard-config.json`; ADR-0002 addendum | last Job succeeded within 5 min; `kubectl -n ci-runners get cm ci-runner-slot-hold -o yaml`; alerts `EstateCINode8GuardTripped` and `EstateCINode8GuardFailing` | Prometheus `observability/prometheus-ha-query` (monitoring-stack admits it); the agents arc-validation-pool reconciler applies the hold to `agentfleet.vallery.net/ci-runner-slot`; EDGE-WRITE.lock to sync the NetworkPolicy |
| ARC scale sets (`AutoscalingRunnerSet` + chart SA/Role/RoleBinding), one Argo Application each | `runners.yaml` → `deploy/runners/apps/` | `kubectl -n ci-runners get autoscalingrunnersets`; Argo Synced/Healthy | `arc-systems` controller (jvallery/agents), `ghcr.io/actions/actions-runner-controller-charts` |
| Argo Applications `estate-ci-foundation`, `estate-ci-runners`, `estate-ci-secrets`; AppProject `estate-ci` | declared in jvallery/agents `platform/gitops/projects/estate-ci*.yaml` | Synced/Healthy | jvallery/agents (Argo catalog owner) |
| Secrets `ci-runners/arc-github-app-jvallery`, `arc-github-app-verdifyconsultancy`, `zot-pull`, `zot-push-<repo>` | jvallery/agents `platform/gitops/secrets-ksops/estate-ci/` (central KSOPS) | key names only; never values | GitHub App `vallery-agent-fleet-ci`; zot `cluster-pull` principal |
| Per-repo zot push principals (`ci-<repo>`) | zot ACL + htpasswd in jvallery/storage-infra; consumer copy `ci-runners/zot-push-<repo>` in central KSOPS (jvallery/agents `platform/gitops/secrets-ksops/estate-ci/`), mounted only in that repo's `build` set | push from `image-build.yml` | jvallery/storage-infra |

Not owned here: the ARC controller and the legacy `github-runners` scale sets
(jvallery/agents, retiring under jvallery/agents#6092), zot (jvallery/storage-infra).
