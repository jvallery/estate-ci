# ADR-0003: In-cluster builds, registry credentials and caching

Status: Accepted (2026-11-06). Owner: CI-ARCH; built by CI-PLATFORM, with
storage-infra for registry principals and retention.

## Context

Native CI builds about 49 images for 11 repositories with Kaniko and pushes them
with a shared writer. The registry origin (zot) is capacity-constrained: the
retention lane is driving it toward a fixed budget, and a registry capacity
signal fails builds closed when it is red. R2 forbids GHCR, the Actions cache,
GitHub Environments and OIDC in the build path.

## Decision

**Build engine.** Kaniko as a sidecar in the `build` / `build-check` profiles,
driven through the Pod-local file exchange (no Docker daemon, no socket, no
privileged container). Kaniko runs as root with the default capability set and
RuntimeDefault seccomp, which Pod Security baseline admits. Rootless BuildKit is
not used: it needs an unconfined seccomp or AppArmor profile, which baseline
rejects. A repository's `build.yml` only calls `ci/build.sh` or the estate-ci
`image-build` workflow; the build arguments live in the repository.

**Mirror images.** A Dockerfile that is only `FROM <upstream>@sha256:...` is a
mirror. Mirrors use `crane copy` to the estate path instead of a Kaniko build:
no layer extraction, no multi-GiB ephemeral storage, and the digest stays the
upstream digest (each mirror's pin changes once, at its first copy).

**Registry credential (replaces the GitHub Environment).** Each building
repository gets its own zot principal with write access to only its own image
paths (storage-infra ACL). The credential is a Kubernetes Secret delivered by
central KSOPS and mounted only into that repository's `build` scale set. The
job-started guard (ADR-0005) refuses every job on a `build` set that is not a
push to, or a dispatch on, the default branch of that repository, so PR code
never runs where the credential is mounted. This keeps the credential out of
GitHub entirely, which is what makes it portable to another forge. PR builds
run on `build-check` with `--no-push` and only the cluster pull credential.

**Tags and digests.** Pushes are `<image>:gha-<sha12>`; the build reads the
digest back and outputs `registry/<image>@sha256:...`. Nothing deploys a tag:
Argo CD deploys only digests committed to Git (ADR-0006). No `latest`.

**Fail-closed coupling to registry capacity.** Until the registry capacity signal
is re-homed to storage-infra, `ci/build.sh` checks the same signal and skips the
push (build green, push deferred, logged) when the registry is in a protective
state, matching native behaviour.

**Caching without the GitHub cache.**

| Need | Mechanism |
|---|---|
| Base images | The existing zot pull-through cache (`registry-cache`) as the dependency proxy for upstream registries |
| Docker layers | Kaniko `--cache` with `--cache-repo <registry>/cache/<repo>` only where a repository shows a measured gain; cache paths get a 7-day retention rule in storage-infra, and enabling it needs a capacity projection (L13) |
| Language dependencies | Baked into the repository's CI image (the weather-ci / vault-ci pattern) or the estate toolbox image (ADR-0004). Lockfile-hash rebuilds of the CI image replace the Actions cache. |
| Persisted outputs | Only images (registry) and digests (Git). Test reports go to the job log and step summary. Anything that must persist goes to the registry as an OCI artifact or to Garage through a repository script, never to the Actions artifact service. |

A PyPI/npm pull-through proxy is not built now; runner egress to public 443
already works. It is a backlog item if egress becomes a constraint.

## Consequences

- storage-infra issues one principal per building repository and removes the
  shared native writer from each path once that repository cuts over.
- The estate-ci `image-build` workflow and the runbook switch from the
  `registry` Environment to the mounted Secret (follow-up for CI-PLATFORM before
  the first build cutover).
- Mirror pins change once per mirror at its first `crane copy`.
