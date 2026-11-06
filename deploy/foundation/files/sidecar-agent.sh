#!/bin/sh
# estate-ci sidecar agent.
#
# Runs inside a sidecar container of an ARC runner Pod (kaniko, job image).
# The runner container drops a request directory into the shared emptyDir
# /exchange/<sidecar>/<id>/ with:
#   script  - POSIX shell script to run (written by actions/sidecar-run)
#   cwd     - working directory (the runner workspace is mounted at the same path)
#   ready   - created last; the agent starts the request when it appears
# The agent writes:
#   started - when it picks the request up
#   log     - combined stdout/stderr
#   rc      - exit status, written atomically after the script finishes
#
# There is no network listener, Kubernetes token or Docker socket: the only
# interface is this Pod-local directory. Busybox compatible (kaniko debug image).
set -u
name="${CI_SIDECAR_NAME:?CI_SIDECAR_NAME is required}"
ex="${CI_EXCHANGE_DIR:-/exchange}/${name}"
mkdir -p "$ex"
: > "$ex/.alive"
echo "estate-ci sidecar agent '${name}' watching ${ex}"
while :; do
  for ready in "$ex"/*/ready; do
    [ -e "$ready" ] || continue
    dir="${ready%/ready}"
    [ -e "$dir/started" ] && continue
    : > "$dir/started"
    cwd="$(cat "$dir/cwd" 2>/dev/null || echo /)"
    echo "running request $(basename "$dir") in ${cwd}"
    ( cd "$cwd" && exec sh "$dir/script" ) > "$dir/log" 2>&1
    rc=$?
    echo "$rc" > "$dir/rc.tmp" && mv "$dir/rc.tmp" "$dir/rc"
    echo "request $(basename "$dir") finished rc=${rc}"
  done
  sleep 1
done
