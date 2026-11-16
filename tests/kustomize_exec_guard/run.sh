#!/usr/bin/env bash
# Self-test for deploy/foundation/bin/kustomize-exec-guard.py (estate-platform#91 stage 1).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../../deploy/foundation/bin/kustomize-exec-guard.py"
fx="$here/fixtures"
failed=0
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

expect() { # expect <label> <want rc> <root>
  local label="$1" want="$2" rc=0
  python3 "$guard" "$3" > "$log" 2>&1 || rc=$?
  if ((rc == want)); then echo "ok   $label"; else echo "FAIL $label (rc=$rc, want $want)"; sed 's/^/     /' "$log"; failed=1; fi
}

expect "ksops exec generator and builtin generator pass" 0 "$fx/good"
expect "ksops kind with a non-ksops exec path is refused" 1 "$fx/exec-sh"
expect "non-builtin transformer config is refused" 1 "$fx/plugin"
expect "inline exec generator is refused" 1 "$fx/inline"
expect "remote validator is refused" 1 "$fx/remote"
expect "whole fixture tree fails" 1 "$fx"
expect "missing root is a usage error" 2 "$fx/nope"
if ((failed)); then echo "kustomize-exec-guard self-test: FAILED"; exit 1; fi
echo "kustomize-exec-guard self-test: OK"
