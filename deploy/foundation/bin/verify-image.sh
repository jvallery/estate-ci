#!/usr/bin/env bash
# estate-ci#125 (ADR-0012): verify that image digests carry a signature from the estate
# image-signing key.
#
#   verify-image.sh [--key FILE] [--plain-http] HOST/IMAGE@sha256:... [...]
#
# --key defaults to keys/estate-image-signing.pub of the estate-ci checkout this script is in.
# Registry credentials come from DOCKER_CONFIG as for any cosign or crane call; --plain-http is for
# the in-cluster origin. No transparency log is consulted (the build path writes none). Prints one
# "verify: ok|FAIL <ref>" line per ref; exits 0 only when every ref verifies, 1 when any fails,
# 2 on a usage error.
set -euo pipefail
key="$(cd "$(dirname "$0")" && pwd)/../../../keys/estate-image-signing.pub" plain=() refs=()
while (($#)); do
  case "$1" in
    --key) key="$2"; shift 2 ;;
    --plain-http) plain=(--allow-http-registry); shift ;;
    -*) echo "verify: unknown option $1" >&2; exit 2 ;;
    *) refs+=("$1"); shift ;;
  esac
done
cosign="${COSIGN:-cosign}"
((${#refs[@]})) || { echo "usage: verify-image.sh [--key FILE] [--plain-http] HOST/IMAGE@sha256:<64 hex> ..." >&2; exit 2; }
[[ -r "$key" ]] || { echo "verify: public key ${key} unreadable (pass --key)" >&2; exit 2; }
command -v "$cosign" >/dev/null || { echo "verify: cosign not found" >&2; exit 2; }
for r in "${refs[@]}"; do
  [[ "$r" =~ ^[A-Za-z0-9.:_/-]+@sha256:[0-9a-f]{64}$ ]] || { echo "verify: ${r} is not a digest ref" >&2; exit 2; }
done
failed=0 err="$(mktemp)"; trap 'rm -f "$err"' EXIT
for r in "${refs[@]}"; do
  if "$cosign" verify --key "$key" --insecure-ignore-tlog=true "${plain[@]+"${plain[@]}"}" "$r" >/dev/null 2>"$err"; then
    echo "verify: ok ${r}"
  else
    echo "verify: FAIL ${r}: $(grep -v -i 'warning' "$err" | tail -n 1 | cut -c1-200)"
    failed=$((failed + 1))
  fi
done
((failed == 0)) || { echo "verify: ${failed} of ${#refs[@]} ref(s) failed" >&2; exit 1; }
