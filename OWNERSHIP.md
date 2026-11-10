# OWNERSHIP: jvallery/estate-ci

Owner repo for the estate's standard CI/CD substrate. Registered in
jvallery/agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`
(the ownership record and its render path are planned to move to
jvallery/estate-platform).

| Item | Where | Health / how to check | Depends on |
|---|---|---|---|
| Reusable workflows `checks.yml`, `image-build.yml`, `build-plan.yml`, `kustomize-validate.yml`, `pin-bump.yml`, `lint.yml`, `mirror.yml` | `.github/workflows/` | `self-test` workflow green on `main` | GitHub Actions |
| Runner scripts (`image-build.sh`, `build-plan.py`, `kustomize-validate.sh`, `pin-bump.sh`, `pin-rewrite.py`, `forge-api.sh`, `app-token.py`, `mirror.sh`, `lint-workflows.py`, `lint-images.py`, `sidecar-run.sh`, `sidecar-agent.sh`, `wait-postgres.sh`, `check-runner-labels.sh`, `job-started-guard.sh`, `hot-guard.py`) | `deploy/foundation/bin/` → ConfigMap `estate-ci-scripts` at `/opt/estate-ci` | `self-test` | — |
| Job image `estate-ci-toolbox` and estate-ci's own base mirrors | `images/estate-ci-toolbox/`, `build.yml`; `mirrors.txt`, `mirror-images.yml` → `registry.vallery.net/jvallery/estate-ci/toolbox` | `build` and `mirror-images` runs green | zot (jvallery/storage-infra), `zot-push-estate-ci` |
| Namespace `ci-runners` (PSA enforce baseline) | `deploy/foundation/namespace.yaml` | `kubectl get ns ci-runners --show-labels` | Argo `estate-ci-foundation` |
| ResourceQuotas `estate-ci-runners` (object caps), `estate-ci-runner-pods` (runner envelope), `estate-ci-ops` (hot guard), LimitRange `estate-ci-defaults` | `deploy/foundation/quota.yaml` | `kubectl -n ci-runners describe quota` | — |
| NetworkPolicies `default-deny`, `runner-egress` | `deploy/foundation/networkpolicies.yaml` | runner jobs reach GitHub and zot | EDGE-WRITE.lock to sync |
| node8 hot guard (D-67): CronJob `ci-runner-hot-guard`, ServiceAccount, Roles/RoleBindings `ci-runner-hot-guard` and `ci-runner-slot-hold-reader` (the agents reconciler reads the hold), NetworkPolicy `hot-guard-egress`, ConfigMaps `ci-runner-hot-guard`, `ci-runner-hot-guard-config` and the guard-created `ci-runner-slot-hold` | `deploy/foundation/hot-guard.yaml`, `hold-reader.yaml`, `bin/hot-guard.py`, `hot-guard-config.json`; ADR-0002 addendum | last Job succeeded within 5 min; `kubectl -n ci-runners get cm ci-runner-slot-hold -o yaml`; alerts `EstateCINode8GuardTripped` and `EstateCINode8GuardFailing` | Prometheus `observability/prometheus-ha-query` (monitoring-stack admits it); the agents arc-validation-pool reconciler applies the hold to `agentfleet.vallery.net/ci-runner-slot` (planned to move here, estate-ci#76); EDGE-WRITE.lock to sync the NetworkPolicy |
| ARC scale sets (`AutoscalingRunnerSet` + chart SA/Role/RoleBinding), one Argo Application each | `runners.yaml` → `deploy/runners/apps/` | `kubectl -n ci-runners get autoscalingrunnersets`; Argo Synced/Healthy | `arc-systems` controller (jvallery/agents today), `ghcr.io/actions/actions-runner-controller-charts` |
| Argo Applications `estate-ci-foundation`, `estate-ci-runners`, `estate-ci-secrets`; AppProjects `estate-ci`, `estate-ci-apps`, `estate-ci-secrets` | declared in jvallery/agents `platform/gitops/projects/estate-ci*.yaml` (planned to move to jvallery/estate-platform) | Synced/Healthy | jvallery/agents (Argo catalog owner today) |
| Secrets `ci-runners/arc-github-app-jvallery`, `zot-pull`, `zot-push-<repo>` | jvallery/agents `platform/gitops/secrets-ksops/estate-ci/` (central KSOPS; planned to move to jvallery/estate-platform) | key names only; never values | GitHub App `vallery-ci-runners`; zot `cluster-pull` principal |
| Per-repo zot push principals (`ci-<repo>`) | zot ACL + htpasswd in jvallery/storage-infra; consumer copy `ci-runners/zot-push-<repo>` in central KSOPS (jvallery/agents `platform/gitops/secrets-ksops/estate-ci/`), mounted only in that repo's `build` set | push from `image-build.yml` | jvallery/storage-infra |
| Pin-PR identity (D-52): GitHub App `vallery-pin-bot`, Actions secret `PIN_BOT_PRIVATE_KEY` and variable `PIN_BOT_CLIENT_ID` in caller repos | `pin-bump.yml`, `deploy/foundation/bin/app-token.py`; key escrow in jvallery/storage-infra `inventory/recovery-key-custody/pin-pr-app-20261106/` | pin PRs opened by the App run `pull_request` CI | GitHub App installation on `jvallery` |

Not owned here: the ARC controller and the ARC foundation (`arc-runner-foundation`,
`ci-runner-slots`), still in jvallery/agents and planned to move here
(estate-ci#76); the legacy `github-runners` scale sets (jvallery/agents, planned
for decommission); zot (jvallery/storage-infra).
