# estate-ci architecture decision records

Program: the estate-wide CI/CD refactor. Every repository with an active
deployment moves off the jvallery/agents native CI (Argo Workflows/Events,
`.agent-fleet/ci.yaml`) onto GitHub Actions, and every job runs on self-hosted
ARC runners in the estate cluster. The design is meant to make a later move to a
self-hosted forge (phase 2) mechanical.

Program owner: lane CI-ARCH. Program epic and issue tree: jvallery/agents
(see ADR-0001). The foundation is built by lane CI-PLATFORM in this repository.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-program-scope-and-rules.md) | Program scope, binding rules (R1-R3) and decisions (J-1..J-7) | Accepted |
| [0002](0002-runner-topology-and-capacity.md) | Runner topology, profiles, quotas and capacity model | Accepted |
| [0003](0003-builds-registry-and-caching.md) | In-cluster builds, registry credentials and caching without GitHub services | Accepted |
| [0004](0004-toolchains-and-test-services.md) | Toolchain images and test-service patterns | Accepted |
| [0005](0005-security-model.md) | Security model: runner isolation, forks, secrets, egress | Accepted |
| [0006](0006-pins-and-delivery.md) | Digest pins, the forge wrapper and Argo CD delivery | Accepted |
| [0007](0007-observability.md) | Runner and job observability | Accepted |
| [0008](0008-cutover-parity-rollback.md) | Cutover, parity, ruleset switch, rollback and decommission order | Accepted |
| [0009](0009-phase-2-forge.md) | Phase 2: self-hosted forge selection and portability rules | Proposed (research) |

The per-repo dependency matrix carries internal detail (namespaces, credential
names, pin files), so it lives in the private jvallery/agents repository at
`docs/estate/ci-refactor/DEPENDENCY-MATRIX.md`, not in this public repository.
These ADRs keep to design and name no addresses or credential values.
