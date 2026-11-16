#!/usr/bin/env bash
# estate-ci#125 / ADR-0012 step 3 (warn mode): generate a CycloneDX SBOM for a pushed digest with the
# Trivy binary and DB cache the CVE gate already used, and attach it to that digest in zot as an OCI
# referrer (artifact type application/vnd.cyclonedx+json), so it is retrievable and retained by digest.
#
#   sbom-attach.sh --trivy BIN --oras BIN --cache DIR --registry-config FILE --ref ORIGIN/IMAGE@sha256:...
#
# Warn mode: every failure logs "sbom: warn ..." and exits 0, so no build fails on this step until
# ADR-0012 step 5 commits an enforce date. Signing (cosign, key-based) is a later step: it needs the
# estate image-signing key, which Jason seals.
set -uo pipefail

trivy="" oras="" cache="" regcfg="" ref=""
while (($#)); do
  case "$1" in
    --trivy) trivy="$2"; shift 2 ;;
    --oras) oras="$2"; shift 2 ;;
    --cache) cache="$2"; shift 2 ;;
    --registry-config) regcfg="$2"; shift 2 ;;
    --ref) ref="$2"; shift 2 ;;
    *) echo "sbom: warn unknown argument $1" >&2; exit 0 ;;
  esac
done
ARTIFACT_TYPE=application/vnd.cyclonedx+json

warn() { echo "sbom: warn $* (not attached; warn mode)"; exit 0; }
[[ "$ref" =~ ^[A-Za-z0-9.:_/-]+@sha256:[0-9a-f]{64}$ ]] || warn "invalid --ref"
[[ -x "$trivy" ]] || warn "trivy unavailable"
[[ -x "$oras" ]] || warn "oras unavailable"
[[ -d "$cache" ]] || warn "trivy cache missing"
[[ -r "$regcfg" ]] || warn "registry config unreadable"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
sbom="$work/sbom.cdx.json"
DOCKER_CONFIG="$(dirname "$regcfg")" "$trivy" --cache-dir "$cache" --quiet image --skip-db-update \
  --skip-java-db-update --insecure --scanners vuln --timeout 15m --format cyclonedx --output "$sbom" "$ref" >&2 \
  || warn "trivy cyclonedx failed"
jq -e '.bomFormat == "CycloneDX"' "$sbom" >/dev/null 2>&1 || warn "trivy output is not CycloneDX"
components="$(jq '.components // [] | length' "$sbom")"
sbom_sha="$(sha256sum "$sbom" | cut -c1-64)"

# The origin is plain HTTP inside the cluster (crane uses --insecure for the same reason).
referrer="$(cd "$work" && "$oras" attach --plain-http --registry-config "$regcfg" \
  --artifact-type "$ARTIFACT_TYPE" --format go-template='{{.digest}}' \
  "$ref" "sbom.cdx.json:${ARTIFACT_TYPE}" 2>"$work/attach.err")" \
  || warn "oras attach failed: $(tail -1 "$work/attach.err" | cut -c1-200)"
[[ "$referrer" =~ ^sha256:[0-9a-f]{64}$ ]] || warn "unexpected attach output"

# Readback: the referrer must be listed for the subject digest.
found="$("$oras" discover --plain-http --registry-config "$regcfg" --artifact-type "$ARTIFACT_TYPE" \
  --format json "$ref" 2>/dev/null | jq -r --arg d "$referrer" \
  '[(.referrers // .manifests // [])[] | select(.digest == $d)] | length')"
[[ "$found" == 1 ]] || warn "referrer ${referrer} not listed by discover"
echo "sbom: attached ${referrer} to ${ref##*@} (cyclonedx, ${components} components, sbom sha256 ${sbom_sha})"
