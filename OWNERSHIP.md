# OWNERSHIP: jvallery/estate-ci

Owner repo for the estate's standard CI/CD substrate. Registered in
jvallery/agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`.

| Item | Where | Health / how to check | Depends on |
|---|---|---|---|
| Reusable workflows `checks.yml`, `image-build.yml`, `pin-bump.yml` | `.github/workflows/` | `self-test` workflow green on `main` | GitHub Actions |
| Composite actions `sidecar-run`, `setup-crane` | `actions/` | `self-test` | — |
| Namespace `ci-runners` (PSA enforce baseline) | `deploy/foundation/namespace.yaml` | `kubectl get ns ci-runners --show-labels` | Argo `estate-ci-foundation` |
| ResourceQuota `estate-ci-runners`, LimitRange `estate-ci-defaults` | `deploy/foundation/quota.yaml` | `kubectl -n ci-runners describe quota` | — |
| NetworkPolicies `default-deny`, `runner-egress` | `deploy/foundation/networkpolicies.yaml` | runner jobs reach GitHub and zot | EDGE-WRITE.lock to sync |
| ConfigMap `estate-ci-scripts` (sidecar agent, job guard) | `deploy/foundation/files/` | present in `ci-runners` | — |
| ARC scale sets (`AutoscalingRunnerSet` + chart SA/Role/RoleBinding), one Argo Application each | `runners.yaml` → `deploy/runners/apps/` | `kubectl -n ci-runners get autoscalingrunnersets`; Argo Synced/Healthy | `arc-systems` controller (jvallery/agents), `ghcr.io/actions/actions-runner-controller-charts` |
| Argo Applications `estate-ci-foundation`, `estate-ci-runners`, `estate-ci-secrets`; AppProject `estate-ci` | declared in jvallery/agents `platform/gitops/projects/estate-ci*.yaml` | Synced/Healthy | jvallery/agents (Argo catalog owner) |
| Secrets `ci-runners/arc-github-app-jvallery`, `arc-github-app-verdifyconsultancy`, `zot-pull` | jvallery/agents `platform/gitops/secrets-ksops/estate-ci/` (central KSOPS) | key names only; never values | GitHub App `vallery-agent-fleet-ci`; zot `cluster-pull` principal |
| Per-repo zot push principals (`ci-<repo>`) | zot ACL + htpasswd in jvallery/storage-infra; consumer copy in each repo's GitHub environment `registry` | push from `image-build.yml` | jvallery/storage-infra |

Not owned here: the ARC controller and the legacy `github-runners` scale sets
(jvallery/agents, retiring under jvallery/agents#6092), zot (jvallery/storage-infra).
