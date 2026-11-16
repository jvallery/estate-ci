# ADR-0012: Image signatures and SBOMs at the image-build chokepoint

Status: Proposed (2026-11-16, estate-ci#125). Owner: estate-ci. This is the plan
PR for estate-ci#125. Implementation follows in separate PRs after review.

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

## Decision (proposed)

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

**Signature and provenance.** `cosign sign --key` signs the pushed digest. A
`cosign attest --type cyclonedx` attestation carries the SBOM, and a minimal
in-toto provenance predicate records:
- source repository and commit (the OCI labels Kaniko already sets);
- workflow run URL;
- builder: the estate-ci reusable workflow and its SHA.

Signatures and attestations are stored as OCI referrers next to the image. The
cosign binary is pinned by version and checksum, the same way as trivy and crane.

**Key custody.** One estate image-signing key pair. The private key is a
KSOPS-encrypted Secret in `ci-runners`, mounted only into the `build` profile's
Kaniko-free signing step: the job sidecar, never the runner container, so
workflow steps cannot read it. Jason generates and seals it through the
hidden-prompt helper (no agent mints it). Its escrow follows the age-identity
escrow in home-dotfiles. The public key is published in this repository and in
the estate-platform ownership record, so any verifier can pin it. Rotation
re-signs nothing: old signatures stay valid under the old public key until its
images age out of retention.

**Mirrors.** Upstream mirrors are not re-signed in phase 1. Their integrity
control stays the upstream digest pin and the zot push ACL. If a later decision
includes mirrors, the same step goes into `mirror.sh` and signs the copied
upstream digest.

**Failure mode.** Warn first, like the CVE gate. A missing key or a signing
failure logs and passes until a committed `sign_enforce_after` date, then fails
the build, so no pin PR is opened for an unsigned digest. SBOM generation
failure follows the same rule.

**Verification (no admission yet).** A reusable `verify-image.yml` and the script
`verify-image.sh` run `cosign verify` and `cosign verify-attestation` against the
published public key. Owners and the security report use them. Admission
enforcement (a policy controller with the zot origin down) is a separate
decision under STD-CI-14.

## Rollout

1. This ADR (plan PR). Review against STD-CI-04 and D-455.
2. Jason seals the signing key (a JASON-TODO step); the public key PR follows.
3. image-build.sh change in warn mode, proven on one builder (docs or
   estate-ci's own images). A digest from that builder must verify, and its SBOM
   must be retrievable from zot.
4. Builder repos bump the reusable pin through their own lanes. The estate-ci#125
   acceptance names four running images to verify after their owners bump:
   vault-server, kube-vip, a verdify-prod image and teslamate.
5. Enforce after the committed date.

## Rollback

Revert the image-build.sh change. Pins and images are unaffected; existing
signatures and SBOM referrers stay in zot and are ignored.

## Not decided here

- The admission verifier (STD-CI-14).
- Signing upstream mirrors.
- The register update that states which STD-CI-04 controls are in force. That
  update lands with step 3, when the build path actually does it.
