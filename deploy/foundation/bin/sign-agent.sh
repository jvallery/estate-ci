#!/usr/bin/env bash
# estate-ci#125 (ADR-0012): the "sign" sidecar of every <repo>-build runner Pod.
#
# It holds the estate image-signing key (/run/secrets/image-signing) and the repo's zot push
# credential (/run/secrets/zot-push); the runner container holds no signing key. Unlike
# sidecar-agent.sh it runs no script: a request directory /exchange/sign/<id>/ written by
# sidecar-run.sh is accepted only when its script is exactly
#   set -e
#   export SIGN_REF='<origin>/<image>@sha256:<64 hex>'
# (what image-build.sh sends after the CVE gate passes, with ENV_NAMES=SIGN_REF and empty stdin).
# Anything else is refused with rc 2. The agent then runs sign-image.sh on that ref and writes
# log and rc like sidecar-agent.sh.
set -u
ex="${CI_EXCHANGE_DIR:-/exchange}/sign"
ORIGIN="${ESTATE_CI_REGISTRY_ORIGIN:-registry-origin.registry-origin.svc.cluster.local:5000}"
KEY_DIR="${ESTATE_CI_SIGNING_KEY_DIR:-/run/secrets/image-signing}"
ZOT_AUTH="${ESTATE_CI_ZOT_AUTH_DIR:-/run/secrets/zot-push}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ex"
# The runner (uid 1001, the Pod fsGroup) drops requests here.
chmod 2770 "$ex" 2>/dev/null || true
: > "$ex/.alive"
echo "estate-ci sign agent watching ${ex}"

serve() { # dir
  local dir="$1" body ref want
  body="$(cat "$dir/script" 2>/dev/null)" || { echo "sign: refused: no script"; return 2; }
  ref="$(sed -n "2s/^export SIGN_REF='\\(.*\\)'\$/\\1/p" <<<"$body")"
  want="$(printf "set -e\nexport SIGN_REF='%s'" "$ref")"
  if [[ -z "$ref" || "$body" != "$want" ]]; then
    echo "sign: refused: the request is not a single SIGN_REF"; return 2
  fi
  if [[ "$ref" != "${ORIGIN}/"* || ! "${ref#"${ORIGIN}/"}" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$ ]]; then
    echo "sign: refused: ${ref} is not an origin digest ref"; return 2
  fi
  bash "$here/sign-image.sh" --key-dir "$KEY_DIR" --registry-config "$ZOT_AUTH/config.json" --plain-http --ref "$ref"
}

while :; do
  for ready in "$ex"/*/ready; do
    [[ -e "$ready" ]] || continue
    dir="${ready%/ready}"
    [[ -e "$dir/started" ]] && continue
    : > "$dir/started"
    echo "sign request $(basename "$dir")"
    serve "$dir" > "$dir/log" 2>&1
    rc=$?
    echo "$rc" > "$dir/rc.tmp" && mv "$dir/rc.tmp" "$dir/rc"
    echo "sign request $(basename "$dir") finished rc=${rc}"
  done
  sleep 1
done
