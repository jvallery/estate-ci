# Public keys

`estate-image-signing.pub` is the public half of the estate image-signing key pair (ADR-0012,
`docs/adr/0012-image-signing-and-sbom.md`). The private key is the KSOPS Secret
`ci-runners/estate-image-signing-key` (sealed 2026-11-17). Verifiers pin this file:

    cosign verify --key keys/estate-image-signing.pub <image@digest>

Rotation: add the new public key next to this one. Do not delete an old key while images signed with it are still
in retention.
