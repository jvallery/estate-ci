# ADR-0009: Phase 2 self-hosted forge, and the portability rules for phase 1

Status: Proposed (research). Owner: CI-ARCH. Phase 2 has its own epic.

## Candidates

| | Forgejo (or Gitea) + Forgejo Actions | GitLab CE + GitLab Runner (k8s executor) | Woodpecker CI (with Forgejo as the forge) |
|---|---|---|---|
| Workflow syntax | GitHub Actions YAML, "familiar, not identical": most keys work; job `permissions` and `continue-on-error` are ignored, some `github` context keys are missing, OIDC is opt-in by a different key | Different language (`.gitlab-ci.yml`); every workflow rewritten | Different language (`.woodpecker/*.yaml`); every workflow rewritten |
| Reusable workflows | Supported (same and remote repositories on the instance; verify version-specific limits in the spike) | `include:` templates and components | Not equivalent; YAML anchors / plugins |
| Runner on k8s | Forgejo Runner; no ARC-like autoscaler. Docker-in-Docker needs privileged Pods (violates baseline). Use host mode in a runner Pod (our sidecar pattern) or a fixed pool; autoscaling by queue needs a small controller or KEDA | Mature k8s executor: one Pod per job, no privilege, autoscaling built in | Native k8s backend: one Pod per step, no privilege |
| Forge features (PRs, rulesets, webhooks, Apps) | PRs, branch protection, webhooks, OAuth2 apps, Authentik OIDC login; no GitHub App equivalent (tokens/OAuth instead) | Full, heaviest | Needs a forge (Forgejo) |
| Argo CD | Plain git repoURL over HTTPS/SSH; webhooks supported | Same | Same (via the forge) |
| Footprint on this cluster | One Go service + Postgres (CNPG) + object storage (Garage): about 0.5-1 GiB | Rails, Sidekiq, Gitaly, Postgres, Redis, registry: about 6-8 GiB minimum; high ops burden | Small server + agents, plus Forgejo |
| HA / backup | Forgejo is single-writer; HA = CNPG + Garage + PBS backup of repos, RPO minutes; restore drill required | Complex HA; backup tooling built in | Stateless-ish server + Forgejo |
| Migration effort from phase 1 | Low: same YAML, swap `forge` wrapper and runner deployment, rename `.github/workflows` (Forgejo also reads `.github/workflows` when `.forgejo` is absent: verify) | High: rewrite all workflows and the runner layer | High: rewrite all workflows |

## Recommendation

**Forgejo with Forgejo Actions**, runners in `ci-runners` using the same
Pod shape (runner + Kaniko / Postgres / job sidecars, no Docker), registration
tokens from KSOPS, and a small queue-driven scaler (or a fixed pool sized by
ADR-0002) in place of ARC. GitLab is rejected on footprint for memory-tight
workers and on a full rewrite; Woodpecker is rejected on rewrite cost and on
needing a forge anyway. Phase 2 starts with a spike (one repository mirrored,
its workflows run unmodified) that confirms the version-specific items marked
"verify".

## Portability rules (binding now, checked by the estate-ci lint)

1. `runs-on` is a single estate label; no hosted labels (R1).
2. `uses:` is only `actions/checkout` or `jvallery/estate-ci/...@<sha>`. The
   checkout action is mirrored by Forgejo's default actions source.
3. Every `run:` step calls a repository script; inline shell is at most a few
   lines of glue. Scripts must run outside Actions given the documented env.
4. No GitHub-only services: no Environments, OIDC, Actions cache, artifact
   service for persisted outputs, GHCR, Apps tokens, `pull_request_target`,
   `workflow_run`, `merge_group`-only logic, `services:` or `container:` keys.
5. Read only these contexts in YAML: `github.repository`, `github.ref`,
   `github.ref_name`, `github.sha`, `github.event_name`, `github.event.pull_request.number`,
   `github.event.pull_request.head.sha`, `inputs.*`, `needs.*`, `matrix.*`.
   Scripts read the equivalent env vars.
6. Do not depend on job-level `permissions` for safety (Forgejo ignores them);
   safety comes from the guard, the credential split and the token scope.
7. All forge API calls go through the `forge` wrapper (ADR-0006).
8. Credentials live in Kubernetes Secrets mounted by the runner profile, never
   in forge secrets, except the per-job token.
9. Required-check names are stable job ids, so a ruleset can be recreated on
   another forge from a list.
10. Each estate-ci reusable workflow carries a "Forgejo notes" header (R3).
