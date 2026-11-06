# ADR-0005: Security model

Status: Accepted (2026-11-06). Owner: CI-ARCH; enforced by CI-PLATFORM.

## Context

Self-hosted runners execute repository code inside the estate cluster. Several
repositories are public (including weather, this repository and
verdify-platform), so anyone can open a pull request. GitHub's guidance is that
self-hosted runners on public repositories are dangerous, because a fork pull
request can run arbitrary code on them.

## Threats and controls

| Threat | Control |
|---|---|
| Fork PR runs code on a runner | Repository setting "require approval for all outside collaborators" on every repository; **and** the job-started guard (a ConfigMap script that a PR cannot change) refuses every fork PR on every profile. Fork contributions are re-pushed to a branch by a maintainer after review. |
| `pull_request_target` / `workflow_run` run fork code with base privileges | Refused by the guard on every profile, and banned by the estate-ci lint. |
| A job from another repository lands on a set | Each set accepts only its own repository (or `Owner/*` for an org set); the guard checks `GITHUB_REPOSITORY`. |
| PR code reads a write credential | Credentials exist only on trusted profiles (`build`, `ops`), whose guard admits only `push` or `workflow_dispatch` on the default branch of the same repository. PR builds use `build-check` (no credential). Branch protection/rulesets limit who can push to the default branch. |
| Registry poisoning | Per-repository zot principal limited to its own paths; Argo CD deploys only digests committed to the default branch through a reviewed PR, so a pushed tag never deploys by itself. |
| Runner escape or lateral movement | Pod Security baseline; no privileged containers, hostPath, host network or Docker socket; `automountServiceAccountToken: false` and no RBAC; one job per Pod (ephemeral runners); NetworkPolicy allowing only DNS, public 80/443 (private ranges excluded), the apps ingress for the registry and the registry origin. `ops` sets get one extra, named egress rule each. |
| Secret sprawl in GitHub | No repository or organization Actions secrets in the build path. The only GitHub-side credential is the per-job `GITHUB_TOKEN`, with least-privilege `permissions:` per workflow. Deploy and registry credentials are Kubernetes Secrets from central KSOPS. |
| Supply chain of workflow code | Only `actions/checkout` and estate-ci reusable workflows/actions, pinned by full SHA. This repository is public, secret-free and protected by a PR-only ruleset; its self-test runs on its own ARC set. |
| Over-privileged registration App | The registration App needs repository administration (runner registration) and organization self-hosted runners only. It is renamed or replaced by a dedicated App with only those permissions before the fleet Apps are deleted (J-5). |

**Bootstrap (R1 for estate-ci itself).** estate-ci's self-test runs on an
`estate-ci-ci` set. If the runners are down, the foundation is repaired through
Argo CD from jvallery/agents (which does not depend on estate-ci checks), and
Jason's ruleset bypass is the documented break-glass for an estate-ci merge. No
GitHub-hosted fallback exists.

**Advisory repositories (J-2).** Where protection cannot be enforced, the same
guard and credential split apply; only the merge gate is advisory.

## Consequences

- The fork policy and the guard are checked by the parity checklist (ADR-0008)
  for every repository, public ones first.
- New credential types (for example a deploy key for an `ops` set) need their
  own row in the ownership record and an egress rule in the same PR.
