#!/usr/bin/env bash
# estate-ci: run a shell script inside a sidecar of this runner Pod (job | kaniko).
#
#   sidecar-run.sh <sidecar> < script.sh
#   ENV_NAMES="A B"      runner env vars exported into the sidecar script
#   WORKDIR=<dir>        working directory (default: $PWD; the workspace is shared)
#   TIMEOUT_MINUTES=<n>  default 60
#
# Client half of sidecar-agent.sh: Pod-local files only, no Docker, no Kubernetes API.
set -euo pipefail
sidecar="${1:?usage: sidecar-run.sh <sidecar> < script}"
[[ "$sidecar" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || { echo "invalid sidecar name" >&2; exit 2; }
ex="${CI_EXCHANGE_DIR:-/exchange}/${sidecar}"
for _ in $(seq 1 120); do [[ -e "$ex/.alive" ]] && break; sleep 1; done
[[ -e "$ex/.alive" ]] || { echo "sidecar '${sidecar}' is not running in this runner Pod (wrong runs-on label?)" >&2; exit 2; }

id="$(date +%s)-$$-${RANDOM}"
dir="$ex/$id"
mkdir -p "$dir"
chmod 0770 "$dir"
printf '%s' "${WORKDIR:-$PWD}" > "$dir/cwd"
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
{
  printf 'set -e\n'
  for name in ${ENV_NAMES:-}; do
    [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "invalid env name $name" >&2; exit 2; }
    printf 'export %s=%s\n' "$name" "$(quote "${!name-}")"
  done
  cat
} > "$dir/script"
: > "$dir/ready"

off=0
deadline=$((SECONDS + ${TIMEOUT_MINUTES:-60} * 60))
flush() {
  [[ -f "$dir/log" ]] || return 0
  local size
  size=$(stat -c %s "$dir/log")
  if (( size > off )); then
    # head stops at the byte count stat saw. If the sidecar appended more since then,
    # tail dies of SIGPIPE/EPIPE when head exits ("tail: write error: Broken pipe");
    # under pipefail that killed this client mid-replay and failed jobs whose script
    # passed (storage-infra#1432, 2026-11-07). The next flush prints those bytes, so
    # only head's status (our own stdout) counts.
    tail -c +"$((off + 1))" "$dir/log" 2>/dev/null | head -c "$((size - off))" || {
      local status=("${PIPESTATUS[@]}")
      (( status[1] == 0 )) || return "${status[1]}"
    }
    off=$size
  fi
}
while [[ ! -f "$dir/rc" ]]; do
  flush
  if (( SECONDS > deadline )); then
    echo "sidecar '${sidecar}' request timed out after ${TIMEOUT_MINUTES:-60} minutes" >&2
    exit 124
  fi
  sleep 1
done
flush
rc="$(cat "$dir/rc")"
rm -rf "$dir"
exit "$rc"
