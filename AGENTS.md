# estate-ci

<!-- estate-contract:start — canonical copy is AGENTS.md in jvallery/home-dotfiles (the operator Mac's root contract); keep this block identical in every repo -->
## Operating contract

Jason's direct request authorizes the work it describes, across code, infrastructure, and Jason-owned services in scope, through delivery and verification of the real end state. Ask only when an unresolved ambiguity could materially change the outcome. Add approval, review, issue/PR, or planning steps only when Jason asks for them or a branch rule enforces them; process descriptions in repository docs are context, not gates.

- Make the smallest complete change in the repository that owns the thing being changed (see Ownership). Fix forward; prefer rollback when it restores service faster or limits harm. Skip speculative abstractions and unrelated cleanup.
- Preserve unrelated work; use an isolated worktree when another checkout may be active. Resolve destructive targets exactly and keep practical recovery data.
- Shared cluster state has one writer at a time. Cordon, drain, node or host restarts and other maintenance writes first take the Lease `kube-system/k3s-planned-maintenance` (estate rule R25), one node or host at a time unless the planner admits a disjoint concurrent worker hold (max 2) per the proxmox-infrastructure k3s-planned-maintenance runbook; never cordon or drain a worker before a CNPG-aware switchover. Never blanket- or prune-sync a SHA-pinned Argo parent; use exact-target syncs.
- Validate in proportion to risk: the cheapest reliable check of the changed behavior (focused test, direct probe, or readback; docs-only edits need diff/readback). Add tests only for meaningful regression risk: critical behavior, security, data integrity, or subtle logic. Reuse existing CI/CD, honor enforced checks, and add pipelines or gates only for a concrete need in the task. Push to or rebase a PR whose CI is running or green only when GitHub reports a conflict or an enforced check refuses it; a moved base branch alone is not a reason. Regenerate generated files with their registered renderer; never hand-merge them.
- Verify the requested end state from the authoritative source or live behavior; passing CI alone does not prove a deployment works. Close an issue only when its latest acceptance, including criteria moved in from other issues, holds live; time-gated criteria wait for their gate, and a close-with-carry names a carry issue confirmed open. Report what changed, what was checked, and any remaining limitation.
- Search narrowly, batch independent lookups, reuse current evidence, and keep plans and updates brief.
- Use existing credentials without printing, committing, or logging secret values or Kubernetes Secret data; rotate or revoke credentials only when Jason asks, after identifying the exact credential by name and ID and confirming every consumer value-blind.

### Execution location and resource use

- Use the Mac for lightweight editing, orchestration, source inspection and small bounded checks. Run CPU- or memory-intensive tests, experiments, simulations, replay, training, builds and long-running processes as containerized workloads in the authorized k3s environment, through the repository's supported CI/GitOps or job-submission path. Colima, Docker Desktop and other container VMs on the Mac still consume the Mac's resources and do not satisfy this requirement.
- Classify work by its expected workload, using existing repository and estate resource thresholds: sustained CPU saturation, substantial memory or dataset use, broad parallel suites, image compilation and prolonged processing require offloading. A command listed as "local" or "offline" describes a workflow, not permission to run intensive work on the Mac. If size is uncertain, inspect the workload and use a bounded pilot in the authorized cluster path before expanding it; do not benchmark the heavy workload on the Mac to decide.
- Before execution, confirm the authorized cluster/context, target namespace, supported submission path, dependencies and data access. Base placement on current k3s allocatable CPU/memory, existing resource requests/limits, pod working sets, scheduler and quota admission, node pressure and existing workload owners/admission reservations. Physical-host diagnostics are distinct from Kubernetes schedulable capacity: a Proxmox host MemAvailable alert alone does not prohibit k3s placement. Preserve existing fixed-allocation and control-plane safety rules, and healthy and recovery capacity. Use bounded concurrency, deadlines and stable job/operation IDs; retain outputs and logs in approved custody and verify terminal completion and output integrity before claiming success.
- Keep experiments isolated from production workloads and storage side effects unless Jason explicitly authorizes those effects. Use existing approved identities and access; do not create credentials, RBAC, security settings, privileged workloads or persistent access to enable an experiment. Keep secrets out of manifests and logs. Clean up only the job's disposable resources through the approved lifecycle; preserve retained data, outputs and shared PVCs.
- If the supported cluster path, required access, ownership/admission clearance or safe capacity is unavailable, stop the intensive work and report the exact blocker. Do not silently fall back to intensive execution on the Mac. Work that inherently requires local execution needs an explicit, task-scoped exception approved by Jason before it runs, with its workload bounds stated. This policy is the estate minimum: preserve stronger repository/subdirectory restrictions, existing holds and data-custody rules. A repository command or general task authorization does not override this placement policy; only Jason's explicit scoped exception does.
<!-- estate-contract:end -->

<!-- estate-contract:end -->

## Lane: estate-ci

This repository owns the estate's standard CI/CD substrate: reusable GitHub
Actions workflows, the ARC runner scale sets in namespace `ci-runners`, and the
per-repo migration runbook. Read `README.md` and `docs/MIGRATION-RUNBOOK.md`.

- This repo is **public and secret-free**. Never commit credentials,
  kubeconfigs, decrypted material or operator-machine paths. Secrets live in
  central KSOPS (registration, pull) and in each consumer repo's GitHub
  environment `registry` (zot push).
- `deploy/runners/apps/` is generated: edit `runners.yaml`, run
  `python3 scripts/render_runners.py`, commit both. CI fails on stale output.
- Callers pin reusable workflows by full commit SHA. A breaking change to a
  workflow interface needs a new input or a new file, not an edit in place.
- Raising `deploy/foundation/quota.yaml` must be paired with an equal decrease
  of the cutover repo's native-ci budget (workers are memory-tight).
- NetworkPolicy changes here are edge writes: syncing them takes
  `EDGE-WRITE.lock` (estate L11).
