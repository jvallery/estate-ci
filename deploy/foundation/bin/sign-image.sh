#!/usr/bin/env bash
# estate-ci#125 (ADR-0012): sign one pushed digest with the estate image-signing key and read the
# signature back. Called by sign-agent.sh (the sign sidecar of <repo>-build sets) after the CVE gate
# passes; the one-off re-sign of running first-party digests calls it the same way.
#
#   sign-image.sh --key-dir DIR --registry-config FILE --ref HOST/IMAGE@sha256:... [--plain-http]
#
# DIR holds cosign.key, cosign.password and cosign.pub (Secret ci-runners/estate-image-signing-key).
# Key-based, no OIDC (ADR-0003), and no transparency log or timestamp authority: the signing config
# names no service, so nothing about estate images leaves the registry. cosign stores the signature
# as an OCI referrer of the digest (zot serves the referrers API). Every failure exits non-zero.
set -euo pipefail
key_dir="" regcfg="" ref="" plain=()
while (($#)); do
  case "$1" in
    --key-dir) key_dir="$2"; shift 2 ;;
    --registry-config) regcfg="$2"; shift 2 ;;
    --ref) ref="$2"; shift 2 ;;
    --plain-http) plain=(--allow-http-registry); shift ;;
    *) echo "sign: unknown argument $1" >&2; exit 2 ;;
  esac
done
cosign="${COSIGN:-cosign}"
[[ "$ref" =~ ^[A-Za-z0-9.:_/-]+@sha256:[0-9a-f]{64}$ ]] || { echo "sign: invalid --ref" >&2; exit 2; }
for f in cosign.key cosign.password cosign.pub; do
  [[ -r "$key_dir/$f" ]] || { echo "sign: ${f} unreadable in the key directory" >&2; exit 2; }
done
[[ -r "$regcfg" ]] || { echo "sign: registry config unreadable" >&2; exit 2; }
command -v "$cosign" >/dev/null || { echo "sign: cosign not found" >&2; exit 2; }

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
printf '%s\n' '{"mediaType":"application/vnd.dev.sigstore.signingconfig.v0.2+json","rekorTlogConfig":{},"tsaConfig":{}}' \
  > "$work/signing-config.json"
export DOCKER_CONFIG; DOCKER_CONFIG="$(dirname "$regcfg")"
COSIGN_PASSWORD="$(cat "$key_dir/cosign.password")" "$cosign" sign --key "$key_dir/cosign.key" \
  --signing-config "$work/signing-config.json" "${plain[@]+"${plain[@]}"}" --yes "$ref" >"$work/sign.log" 2>&1 \
  || { tail -n 5 "$work/sign.log" | cut -c1-300 >&2; echo "sign: cosign sign failed for ${ref}" >&2; exit 1; }
"$cosign" verify --key "$key_dir/cosign.pub" --insecure-ignore-tlog=true "${plain[@]+"${plain[@]}"}" "$ref" \
  >"$work/verify.json" 2>"$work/verify.log" \
  || { tail -n 5 "$work/verify.log" | cut -c1-300 >&2; echo "sign: signature readback failed for ${ref}" >&2; exit 1; }
echo "sign: signed ${ref##*@} (${ref%@*}), signature verified against the mounted public key"
