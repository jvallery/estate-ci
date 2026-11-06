# ADR-0004: Toolchain images and test-service patterns

Status: Accepted (2026-11-06). Owner: CI-ARCH; built by CI-PLATFORM.

## Context

The upstream ARC runner image carries git, curl, jq and python3 only. Native CI
steps assume a fleet toolbox (uv, pip, node, ruby, kubectl, kustomize, yq,
gitleaks) or run inside a repository CI image. R2 rules out `actions/setup-*`
and `services:` containers need a Docker daemon, which runners do not have.

## Decision

**Three toolchain sources, in this order of preference:**

1. **The repository's own CI image** (`<repo>-ci`, for example weather-ci,
   vault-ci, the homeassistant CI images), run as the `job` sidecar. The image is
   built by the repository's own `build.yml` and pinned by digest in
   `runners.yaml`.
2. **`estate-ci-toolbox`**: one secret-free image built by estate-ci from a
   mirrored base, containing python3 with pip and uv, node and npm, ruby,
   kubectl, kustomize, yq, jq, crane, gitleaks, shellcheck and actionlint, all
   pinned with checksums. It is the default `job` image for repositories without
   their own CI image. It replaces the fleet `agents-arc-runner-toolbox`.
3. **In-script bootstrap** with pinned versions and checksums (for example a
   single tool a repository needs). Allowed but discouraged; it costs egress and
   time on every run.

**Test services.**

| Need | Pattern |
|---|---|
| Postgres / pgvector | `pg` profile: Postgres sidecar on `127.0.0.1:5432`, emptyDir, fsync off, image from the estate registry. No CNPG cluster or PVC per run. |
| Redis, Timescale or other single services | A sidecar in a repository-specific profile variant declared in `runners.yaml`, same pattern as Postgres. |
| Browsers (Playwright) | Inside the repository CI image, with browsers baked in. |
| `kubectl kustomize` / render checks | Toolbox; no cluster access (no ServiceAccount token). |
| Live cluster or device checks | Not in CI. They stay as in-cluster jobs owned by the repository (or an `ops` set with an explicit credential and egress, ADR-0005). |
| GPU | Never in CI. GPU qualification stays a cluster workload. |
| Firmware/compile-heavy suites (ESPHome) | `ci` profile with a larger, measured job sidecar limit and a path filter. |

`services:` and `container:` keys are not used; they need a Docker daemon and
behave differently on other forges.

**Large suites.** Shard with a matrix of calls to the same script, each shard
under 15 minutes. A summary job named for the required check aggregates the
shards so the required context stays stable when the shard count changes.

## Consequences

- MIGRATE-APPS and MIGRATE-INFRA stop bootstrapping tools per repository once
  the toolbox image exists (CI-PLATFORM follow-up).
- Repository CI images become first-class build outputs of their own repository
  and are cut over first, because their check jobs depend on them.
