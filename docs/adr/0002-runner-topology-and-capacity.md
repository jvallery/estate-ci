# ADR-0002: Runner topology, profiles, quotas and capacity model

Status: Accepted (2026-11-06). Owner: CI-ARCH; built by CI-PLATFORM.

## Context

- The existing ARC controller (gha-runner-scale-set controller, cluster-wide)
  already serves 19 scale sets: 18 in `github-runners`, rendered from the fleet
  registry by a 2,749-line fleet renderer with a fleet toolbox image, plus one in
  `frigate`. Those are fleet dependencies and retire with their repositories.
- `jvallery` is a user account, so its scale sets register per repository.
  VerdifyConsultancy is an organization and can use org-level sets.
- The GPU workers are memory-bound: on 2026-11-06 nodes 4, 5 and 7 had 96-98% of
  allocatable memory requested and 90-98% of CPU. Native CI holds two quotas
  (about 76 CPU / 81 GiB of requests together) and used about 56 CPU / 66 GiB of
  requests at the sample.

## Decision

**Namespace.** All new runners live in `ci-runners` (Pod Security `enforce:
baseline`, `warn/audit: restricted`), rendered from `runners.yaml` by
`scripts/render_runners.py` into one Argo CD Application per scale set. The
`github-runners` sets are not extended; each retires when its repository cuts
over.

**Scale-set naming.** `<repo>-<profile>` for jvallery repositories (the name is
the `runs-on` label); `verdify-<profile>` org-level for VerdifyConsultancy
repositories that stay in the organization. A transferred repository gets its
own repo-level sets in the same PR that moves it.

**Profiles.**

| Profile | Pod | Trust | Default max |
|---|---|---|---|
| `ci` | runner (+ optional `job` sidecar running the repo's CI image) | untrusted (PR code) | 3 per repo |
| `pg` | runner + Postgres (pgvector) sidecar on emptyDir (+ job) | untrusted | 1 per repo, 2 estate-wide |
| `build-check` | runner + Kaniko sidecar, no registry write credential (`--no-push`) | untrusted | 1 per repo |
| `build` | runner + Kaniko sidecar + the repo's registry writer | trusted: default-branch push only (ADR-0005) | 1 per repo |
| `ops` | runner + a named deploy credential and a dedicated egress policy (for example HAOS deploy, Cloudflare Worker deploy) | trusted: default-branch push or dispatch only | 1 per repo |

Profiles are classes, not repos: a repository only gets the profiles its
workflows use. Image mirrors whose Dockerfile is only `FROM <upstream>@<digest>`
do not need Kaniko: they use `crane copy` on a `build` set (ADR-0003), which
removes the heaviest builds (vLLM and GPUStack mirrors) from the build budget.

**Capacity model.**

1. **One envelope.** One ResourceQuota on `ci-runners` bounds every set. It grows
   only by what a cutover removes from `agent-fleet-ci-budget` /
   `agent-fleet-ci-foreign-budget` and from the retiring `github-runners`
   quotas, in the same change set. Target after all cutovers: about 40 CPU /
   64 GiB of requests, below native today because the controller, EventBus,
   sensors and per-run PVC churn disappear.
2. **Requests at typical use, limits at peak.** Runner containers request 250m /
   512 MiB; job sidecars and Kaniko request the measured p50 working set of the
   repo's native runs and limit at about 1.5 × p95. Kaniko memory limits are
   set per image (12 GiB default, more only with measured evidence).
3. **Low priority.** Runner Pods use a negative PriorityClass, so production
   workloads preempt CI, never the reverse. Pods tolerate no taints (node8
   stays Frigate-only; control-plane nodes stay cordoned).
4. **Soft spread only.** No hard hostname anti-affinity (it strands runners);
   a preferred spread across workers is enough.
5. **Ephemeral storage, no PVCs.** Work directories and build contexts use
   emptyDir with ephemeral-storage requests and limits; `persistentvolumeclaims:
   0` in the quota. That removes native CI's 1,300-PVC Longhorn churn.
6. **Concurrency caps.** `maxRunners` per set plus the quota. A queued job waits
   in GitHub, not in the cluster. Heavy suites shard (each shard under 15
   minutes) and share the estate-wide `pg` cap.
7. **Scale from zero.** `minRunners: 0` everywhere; listener Pods (about 10m CPU
   each) in `arc-systems` are the only idle cost.

## Consequences

- Capacity is planned per cutover in the PR that adds a set: the PR states the
  quota increase and the matching native decrease.
- ARC (the controller and the chart) becomes estate platform. Its upgrades are
  owned by jvallery/agents platform (the controller) and estate-ci (scale-set
  chart version in `runners.yaml`).
- If the workers stay above 95% of memory requests, cutovers still proceed
  because they move requests rather than add them; new parallelism waits for
  capacity work (VM RAM, rightsizing) in the hosts lane.
