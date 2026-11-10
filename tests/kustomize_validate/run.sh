#!/usr/bin/env bash
# Self-test for deploy/foundation/bin/kustomize-validate.sh (estate-ci#156). Runs on
# the estate-ci-ci-toolbox set (kustomize, kubeconform; kubeconform's default
# schema location over public 443, as scripts/foundation-check.sh uses).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
kv="$here/../../deploy/foundation/bin/kustomize-validate.sh"
fx="$here/fixtures"
failed=0
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

expect() { # expect <label> <want rc: 0|fail> <args...>
  local label="$1" want="$2" rc=0
  shift 2
  env -u KV_ROOTS -u KV_FORBID_SECRETS -u KV_IGNORE_MISSING_SCHEMAS -u KV_SCHEMA_LOCATIONS \
    -u KV_KUBERNETES_VERSION bash "$kv" "$@" > "$log" 2>&1 || rc=$?
  if { [[ "$want" == 0 ]] && ((rc == 0)); } || { [[ "$want" == fail ]] && ((rc != 0)); }; then
    echo "ok   $label"
  else
    echo "FAIL $label (rc=$rc)"; sed 's/^/     /' "$log"; failed=1
  fi
}

expect "valid root" 0 "$fx/good"
expect "valid root, pinned kubernetes version" 0 --kubernetes-version 1.35.5 "$fx/good"
expect "unknown field fails -strict" fail "$fx/invalid"
expect "kustomize build error fails" fail "$fx/broken"
expect "one bad root fails the run" fail "$fx/good" "$fx/invalid"
expect "Secret allowed by default" 0 "$fx/secret"
expect "--forbid-secrets rejects a Secret" fail --forbid-secrets "$fx/secret"
expect "no roots is a usage error" fail
expect "unknown option" fail --nope "$fx/good"
if KV_ROOTS="$fx/secret" KV_FORBID_SECRETS=true bash "$kv" > "$log" 2>&1; then
  echo "FAIL env inputs (KV_ROOTS, KV_FORBID_SECRETS)"; failed=1
else
  echo "ok   env inputs (KV_ROOTS, KV_FORBID_SECRETS)"
fi
if grep -qF not-a-secret "$log"; then
  echo "FAIL Secret value printed"; failed=1
else
  echo "ok   Secret value never printed"
fi
if ((failed)); then echo "kustomize-validate self-test: FAILED"; exit 1; fi
echo "kustomize-validate self-test: OK"
