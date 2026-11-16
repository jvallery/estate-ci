# ADR-0011: Accepted phase-1 exceptions to the portability and secrets rules

Status: Accepted (2026-11-09). Owner: CI-ARCH. Decisions: D-52 (Jason
first-hand, 2026-11-06) for E-2; D-373 (2026-11-09 sweep, accepted) for E-1's
STD-CI-07 exception row.

## Context

ADR-0009 sets the portability rules that bind phase 1 now, but its own status
is Proposed (research), and it was the only place that recorded the two
exceptions that phase 1 actually runs with. An exception that only a Proposed
ADR records is not an accepted rule. This ADR carries both exceptions under an
Accepted status. The exception ids stay E-1 and E-2, so existing references
(`lint-workflows.py`, the toolbox Dockerfile and README, `runners.yaml`) still
resolve; ADR-0009 keeps a pointer here.

Each exception names its scope, why no portable alternative exists, and what
replaces it on a phase-2 forge. The lint does not police E-1; it allows exactly
the E-2 secret name.

## Exceptions

- **E-1 (2026-11-06, COORD; accepted under D-373, 2026-11-09): upstream
  supply-chain attestation verification.** Rules affected: ADR-0009 rule 7 (all
  forge API calls through the `forge` wrapper) and STD-CI-07.
  The estate-ci-toolbox image ships the `gh` CLI (pinned, sha256-checked) so
  jobs can run `gh attestation verify` on upstream release artifacts (first
  user: sentinel's frigate-promotion jobs, moving off GitHub-hosted runners).
  Scope: verification of upstream GitHub-issued attestations only. Not allowed:
  `gh` for forge API calls (those go through `forge-api.sh`), or any credential
  beyond the per-job token. Phase 2: the upstream attestations stay on GitHub,
  so verification keeps working from any forge (it needs only outbound HTTPS to
  the public Sigstore/GitHub endpoints); if a token-free path is required,
  replace it with `cosign verify-blob-attestation` against the same bundles.
  Review: with the STD-CI-07 exception row's review date.
- **E-2 (2026-11-06, D-52, Jason first-hand): the vallery-pin-bot App key.**
  Rules affected: ADR-0005 "no repository Actions secrets in the build path",
  ADR-0009 rule 4 (no Apps tokens) and rule 8 (no forge secrets beyond the
  per-job token), STD-CI-09.
  Each repository has the Actions secret `PIN_BOT_PRIVATE_KEY` and the variable
  `PIN_BOT_CLIENT_ID`. Callers pass the secret to `pin-bump.yml` as
  `pin-bot-private-key`. `app-token.py` mints an installation token from it,
  scoped to the calling repository with contents and pull_requests write, and
  revokes it when the step exits. The pin PR is opened with that token. A PR
  opened with the per-job token starts no `pull_request` workflows, so its
  required checks never attach. Jason chose Actions-secret custody for this
  key (D-52, 2026-11-06), and lint allows exactly this secret name. It is the
  only Actions secret the estate-ci path permits (D-296 removes the others
  after value-blind consumer checks).
  Custody: storage-infra `inventory/recovery-key-custody/pin-pr-app-20261106/`.
  Non-owner-writer rule (2026-11-16, options-tracker#176; accepted under
  D-696 (1) b, Jason first-hand, which amends D-52): a repository with any
  write, maintain or admin collaborator besides the owner does not receive
  the secret or the variable. Any writer can add a workflow that reads the
  secret, and the installation token it mints is not limited to the calling
  repository unless the workflow scopes it. Such a repository opens pin PRs
  through `pin-bump.yml` with the per-job token, recorded as its STD-CI-13
  exception row. Adding a non-owner writer to a repository that holds the key
  means removing the secret and variable first. On 2026-11-16, 29 of 31
  non-archived jvallery repositories held the key, and options-tracker was
  the only one with non-owner writers; its secret and variable were removed
  that day (readback 21:00Z). D-696 keeps the key unrotated: no exploitation
  is known, and the other holders keep the current key.
  Phase 2: if the forge starts workflows for job-token PRs, drop the key and
  E-2. Otherwise give a forge bot account token the same scope.

## Consequences

- A new exception to an ADR-0009 portability rule or to the ADR-0005 secrets
  rule is added here (or in a later Accepted ADR), with its decision, scope and
  phase-2 replacement, not in ADR-0009.
- When ADR-0009 is accepted or superseded, these exceptions stay in force until
  this ADR is superseded.
