# ADR-0001: Program scope, binding rules and decisions

Status: Accepted (2026-11-06). Owner: CI-ARCH.

**Superseding note (2026-11-09, estate-ci#105).** End-state item 3 ("parity
shown per ADR-0008") was replaced by D-22's hard cut-over (one green Actions
run per repository; D-15 for jvallery/agents), and the consequence "each
cutover moves quota from native CI to ARC" by D-34/D-39 and the native CI
retirement (jvallery/agents#6254, D-141). See the notes in ADR-0002 and
ADR-0008.

## Context

Jason (first-hand, 2026-11-06): one lane researches, architects, designs and
tracks an estate-wide CI refactor that moves everything off fleet CI to GitHub
Actions + ARC. The design must allow a later move to a self-hosted forge. All
work is tracked as GitHub issues; every repository with an active deployment is
researched for constraints and dependencies before its lane starts.

Native CI today validates 28 repositories that carry `.agent-fleet/ci.yaml`
(generated from the jvallery/agents registry), builds about 49 images for 11
repositories, runs bespoke pipelines for the Verdify repositories, and hosts
several estate jobs (registry signal, security scans). It shares one quota and
one `repo-validate` semaphore across all of them, which starves smaller repos.

## Decision

**Scope.** Phase 1: every repository with an active deployment, or with a
`.agent-fleet/ci.yaml`, ends on GitHub Actions running only on ARC runners;
estate jobs hosted by native CI move to their owner repos. Phase 2 (separate
epic, research first): a self-hosted forge (ADR-0009). The platform-repo split
of jvallery/agents is a separate epic (J-3).

**Binding rules** (coordinator steer, 2026-11-06):

- **R1. Zero GitHub-hosted runners.** Every job's `runs-on` is an ARC scale-set
  label. `ubuntu-*`, `macos-*` and `windows-*` are forbidden, including in this
  repository's own self-test (ADR-0005 covers the bootstrap case). A lint in
  estate-ci fails any workflow that names a hosted label, and the Actions
  spending limit is $0 where the plan allows.
- **R2. Thin, portable YAML.** Logic lives in repository scripts (`ci/*.sh`,
  a Makefile or a task file) that run the same on any runner. Allowed `uses:`
  are `actions/checkout` and estate-ci's own reusable workflows and composite
  actions, pinned by full SHA. Not allowed in the build path: marketplace
  actions (including `actions/setup-*`, `actions/cache`,
  `actions/upload-artifact` for anything that must persist,
  `actions/create-github-app-token`), GHCR, GitHub Environments, OIDC. Builds
  call in-cluster Kaniko from a script. Pin bumps use git plus one forge-API
  wrapper (ADR-0006).
- **R3. Portable to a self-hosted forge.** Each estate-ci workflow documents its
  Forgejo Actions notes, and every caller follows the portability rules in
  ADR-0009.

**Decisions** (Jason first-hand unless noted):

| # | Decision |
|---|---|
| J-1 | One copy of the reusable workflows per owner, at most. Reconciled: this repository is public and secret-free, so one copy serves every caller (a public repository cannot call a private reusable workflow, and `jvallery/weather` is public). With D19 every repository with CI is a jvallery repository, so the owner question goes away; no VerdifyConsultancy copy or mirror exists. Secret-freedom is enforced by self-test (no `secrets:` use other than `GITHUB_TOKEN`, no credential files). |
| J-2 | Advisory, non-blocking checks where the plan cannot enforce protection (VerdifyConsultancy private repositories, `jvallery/options-tracker`). The lane merges only on green and says "advisory" in the PR. |
| J-3 | The platform-repo split is its own epic. |
| J-5 | The fleet GitHub Apps are deleted last, after cutover and a zero-dependency check. The ARC registration App is kept (renamed) or replaced first, never deleted while a scale set uses it. |
| J-6 | The jvallery/agents ruleset switches to Actions checks only after parity. |
| J-7 | memoryos image builds (GHCR) are out of scope; its leftover CI files are still cleaned up. |
| Move | verdify-platform, verdify-www and sunshine_club transfer to jvallery (REPO-MOVE lane); their CI cutover is sequenced with the transfer (ADR-0008). |
| D19 | (Jason first-hand, D19, 2026-11-06) (a) Parity fast path: 5 agreeing PRs for merge-gated repositories, 3 runs for static ones, no 48-hour wait (ADR-0008). (b) ARC runners serve **jvallery repositories only**; any VerdifyConsultancy repository that needs CI moves to jvallery first (REPO-MOVE); there are no VerdifyConsultancy scale sets (ADR-0002). (c) jvallery/agents goes onto ARC **first**, right after its estate-ci delivery lands, with the J-6 ruleset switch after fast-path parity (ADR-0008). |

**End state per repository** (the acceptance criteria every migration issue
carries):

1. All jobs run on ARC scale sets (R1); the workflows pass the estate-ci lint (R2).
2. Image builds run in-cluster from `build.yml` and push by digest to the estate
   registry; the pin job is the only pin writer; Argo CD delivers unchanged.
3. Parity shown per ADR-0008; required checks switched by one ruleset write with
   before/after JSON kept (or advisory under J-2).
4. `.agent-fleet/ci.yaml` and fleet-only files removed from the repository; the
   repository's agents catalog row, Sensor and build rows removed.
5. No imports of fleet code, no `fleet-ci/*` contexts on new heads.
6. An independent verifier closes the issue against live evidence (L9).

## Consequences

- Native CI receives no new investment beyond the in-flight throughput fixes.
- Each cutover moves quota from native CI to ARC; it never adds cluster load.
- The program view is a single epic in jvallery/agents that links the
  decommission epic, the foundation issue, one issue per repository, one issue
  per estate job and the phase 2 epic.
