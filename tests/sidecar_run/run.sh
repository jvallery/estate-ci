#!/usr/bin/env bash
# Regression test for sidecar-run.sh's log replay (storage-infra#1432, 2026-11-07).
# A fake sidecar agent writes a burst of output large enough that the log grows
# while the client is replaying it. The client must copy every byte and exit with
# the script's rc. Before the fix, tail died with SIGPIPE/EPIPE when head stopped
# at the stat'ed size, and pipefail turned that into exit 1 mid-replay.
set -euo pipefail
client="$(cd "$(dirname "$0")/../.." && pwd)/deploy/foundation/bin/sidecar-run.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export CI_EXCHANGE_DIR="$work/exchange"
mkdir -p "$CI_EXCHANGE_DIR/job"
: > "$CI_EXCHANGE_DIR/job/.alive"

fake_agent() { # serve one request: write $1 bytes in bursts, then rc $2
  local req
  until req="$(find "$CI_EXCHANGE_DIR/job" -mindepth 2 -maxdepth 2 -name ready -printf '%h' -quit)" && [[ -n "$req" ]]; do sleep 0.1; done
  for _ in $(seq 1 40); do
    head -c "$(( $1 / 40 ))" /dev/zero | tr '\0' 'x' >> "$req/log"
    sleep 0.05
  done
  printf '%s' "$2" > "$req/rc"
}

for rc in 0 3; do
  fake_agent 40000000 "$rc" &
  got=0
  bytes="$(echo true | TIMEOUT_MINUTES=2 "$client" job | wc -c)" || got=$?
  wait
  [[ "$got" == "$rc" ]] || { echo "sidecar-run.sh exited $got, want $rc" >&2; exit 1; }
  [[ "$bytes" == 40000000 ]] || { echo "sidecar-run.sh replayed $bytes bytes, want 40000000" >&2; exit 1; }
done
echo "sidecar-run replay: OK"
