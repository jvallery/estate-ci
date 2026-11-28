# ADR-0012: Image signatures and SBOMs at the image-build chokepoint

Status: Accepted (2026-11-27, estate-ci#125; proposed 2026-11-16). Owner: estate-ci.
Signing is in force with no warn period; the SBOM attach stays in warn mode.

## Context

STD-CI-04 requires built images to be signed, with an SBOM and provenance that
admission verifies. Today the build path produces none of these. Every estate
image is built through one path (STD-CI-12): the reusable `image-build.yml`
workflow, which runs `deploy/foundation/bin/image-build.sh`. That script builds
with Kaniko, pushes with `crane`, reads back the digest and runs the trivy
critical-CVE gate (estate-ci#127) against the pushed digest. Upstream mirrors
take the separate `mirror.sh` path (`crane copy`, upstream digest unchanged).

D-455 (Jason, 2026-11-09) ratified the defaults for D-272. Generation is kept.
Admission enforcement raises no work until a verifier is named (STD-CI-14 is a
proposal). ADR-0003 forbids OIDC in the build path, so keyless (Fulcio) signing
is out and signing must be key-based.

## Decision

**One place.** Generation goes into `image-build.sh`, after the digest readback
and after the CVE gate passes, so a blocked digest is never signed. Every caller
inherits it by bumping its reusable-workflow pin; no builder repository adds
steps of its own.

**SBOM.** Trivy is already downloaded and checksum-verified in the build path, so
it generates the SBOM: `trivy image --format cyclonedx` against
`<origin>/<image>@<digest>`, from the same DB fetch as the gate. No second
scanner and no extra pull. The SBOM is attached to the digest in zot as an OCI
referrer (artifact type `application/vnd.cyclonedx+json`), so it is
retrievable by digest and is retained and garbage-collected with the image.
The attach uses `oras attach` (pinned by version and checksum), followed by an
`oras discover` readback; this is implemented in `deploy/foundation/bin/sbom-attach.sh`
(step 3, warn mode).

**Signature.** `cosign sign --key` signs the pushed digest after a pass verdict
of the CVE gate, only with `--push`; a block verdict (in warn mode too) signs
nothing. The signing config names no transparency log and no timestamp
authority, so nothing about estate images leaves the registry, and verifiers pass
`--insecure-ignore-tlog`. The signature is stored as an OCI referrer of the
digest in zot and read back with `cosign verify` before the digest is output.
cosign v3.1.6 ships in the estate-ci toolbox, built from module source and pinned
by module version and sum like crane. Provenance attestations (`cosign attest`
of the SBOM and an in-toto predicate) are not built yet and are a later decision.

**Key custody.** One estate image-signing key pair. The private key is the
KSOPS-encrypted Secret `estate-image-signing-key` in `ci-runners`, mounted only
into the `sign` sidecar of `<repo>-build` sets (rendered by
`scripts/render_runners.py`), never the runner container. That sidecar runs
`sign-agent.sh`, which runs no script: it accepts only a request naming one
origin digest ref and runs `sign-image.sh` on it, so a workflow step can ask for
a signature but cannot read the key. The runner container carries
`CI_IMAGE_SIGNING=required`, which makes `image-build.sh` fail a push it cannot
sign. Jason generates and seals it through the
hidden-prompt helper (no agent mints it). Its escrow follows the age-identity
escrow in home-dotfiles. The public key is published in this repository and in
the estate-platform ownership record, so any verifier can pin it. Rotation
re-signs nothing: old signatures stay valid under the old public key until its
images age out of retention.

**Mirrors.** Upstream mirrors are not re-signed in phase 1. Their integrity
control stays the upstream digest pin and the zot push ACL. If a later decision
includes mirrors, the same step goes into `mirror.sh` and signs the copied
upstream digest.

**Failure mode.** No warn period for signatures (estate-ci#125): the key exists
and nothing consumes signatures yet, so a signing failure fails the build and
blocks only that digest's pin PR. A runner Pod rendered before the sign sidecar
existed has no `CI_IMAGE_SIGNING` and logs that it skipped signing. SBOM
generation stays warn mode (`sbom-attach.sh`) until an enforce date is committed.

**Verification (no admission yet).** `deploy/foundation/bin/verify-image.sh`
runs `cosign verify --key keys/estate-image-signing.pub` for one or more digest
refs. Owners and the security report use it. Admission enforcement (a policy
controller with the zot origin down) is a separate decision under STD-CI-14,
tracked in the estate's platform repository.

## Rollout

1. This ADR (plan PR). Review against STD-CI-04 and D-455.
2. The signing key is sealed into the estate-ci secrets bundle; the public key PR follows.
3. SBOM attach in warn mode (done).
4. Signing in `image-build.sh` with the sign sidecar, enforced from the start
   (estate-ci#125). Builders need no pin bump: every runner mounts the scripts
   from `main`, and the sign sidecar arrives with the runner-set pin. Every
   first-party digest running at the time is signed once by a one-off run.
5. SBOM enforce after a committed date; provenance attestations; admission.

## Rollback

Revert the image-build.sh change (live within minutes) or move the runner-set
pin back. Pins and images are unaffected; existing signatures and SBOM referrers
stay in zot and are ignored.

## Not decided here

- The admission verifier (STD-CI-14).
- Signing upstream mirrors.
- The register update that states which STD-CI-04 controls are in force. That
  update lands with step 3, when the build path actually does it.
