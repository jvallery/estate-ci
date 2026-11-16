#!/usr/bin/env bash
# estate-ci#127: deploy/foundation/bin/cve-gate.sh against synthetic Trivy reports for every
# acceptance outcome, in enforce mode (exit 1 = block) and once in warn mode (always exit 0).
set -euo pipefail
gate="$(cd "$(dirname "$0")/../.." && pwd)/deploy/foundation/bin/cve-gate.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
D="sha256:$(printf 'a%.0s' {1..64})"
OTHER="sha256:$(printf 'b%.0s' {1..64})"
NOW="2026-11-20T12:00:00Z"
DB="2026-11-20T06:00:00.123456Z"
OWNER="jvallery/example"

report() { # file digest vulns-json
  jq -n --arg d "$2" --argjson v "$3" \
    '{Metadata: {RepoDigests: ["registry-origin:5000/jvallery/example@" + $d]}, Results: [{Target: "x", Vulnerabilities: $v}]}' > "$1"
}
vuln() { jq -n --arg id "$1" --arg s "$2" --arg f "$3" '{VulnerabilityID: $id, Severity: $s, FixedVersion: $f}'; }
FIX="$(vuln CVE-2026-0001 CRITICAL 1.2.3)"
NOFIX="$(vuln CVE-2026-0002 CRITICAL "")"
HIGH="$(vuln CVE-2026-0003 HIGH 2.0)"
row() { # digest cve expires
  jq -n --arg d "$1" --arg c "$2" --arg e "$3" --arg o "$OWNER" \
    '{id: "t", register: "R26", owner: $o, manifest_digest: $d, cves: [$c], expires_at: $e}'
}
jq -n --argjson r "$(row "$D" CVE-2026-0001 2026-12-01T00:00:00Z)" '{exceptions: [$r]}' > "$tmp/active.json"
jq -n --argjson r "$(row "$D" CVE-2026-0001 2026-11-19T00:00:00Z)" '{exceptions: [$r]}' > "$tmp/expired.json"
jq -n --argjson r "$(row "$OTHER" CVE-2026-0001 2026-12-01T00:00:00Z)" '{exceptions: [$r]}' > "$tmp/otherdigest.json"

report "$tmp/clean.json" "$D" "[$HIGH]"
report "$tmp/fix.json" "$D" "[$FIX, $NOFIX]"
report "$tmp/nofix.json" "$D" "[$NOFIX, $HIGH]"
report "$tmp/mismatch.json" "$OTHER" "[$HIGH]"
echo '{"Results": "nope"' > "$tmp/invalid.json"

fails=0
expect() { # name want-rc want-reason args...
  local name="$1" want="$2" reason="$3"; shift 3
  local out rc=0
  out="$(bash "$gate" --digest "$D" --owner "$OWNER" --now "$NOW" "$@" 2>&1)" || rc=$?
  if [[ "$rc" != "$want" || "$out" != *"reason=${reason} "* ]]; then
    echo "FAIL ${name}: rc=${rc} (want ${want}), output: ${out}"; fails=$((fails + 1))
  else
    echo "ok   ${name}: rc=${rc} reason=${reason}"
  fi
}
E=(--mode enforce --db-updated-at "$DB")
expect clean                   0 clean             "${E[@]}" --report "$tmp/clean.json"
expect fixable-blocked         1 fixable           "${E[@]}" --report "$tmp/fix.json"
expect nofix-allowed           0 no-fix-only       "${E[@]}" --report "$tmp/nofix.json"
expect excepted-allowed        0 excepted          "${E[@]}" --report "$tmp/fix.json" --exceptions "$tmp/active.json"
expect expired-blocked         1 expired-exception "${E[@]}" --report "$tmp/fix.json" --exceptions "$tmp/expired.json"
expect row-other-digest        1 fixable           "${E[@]}" --report "$tmp/fix.json" --exceptions "$tmp/otherdigest.json"
expect unreadable-exceptions   1 fixable           "${E[@]}" --report "$tmp/fix.json" --exceptions "$tmp/invalid.json"
expect missing-report          1 report-missing    "${E[@]}" --report "$tmp/none.json"
expect invalid-report          1 report-invalid    "${E[@]}" --report "$tmp/invalid.json"
expect digest-mismatch         1 digest-mismatch   "${E[@]}" --report "$tmp/mismatch.json"
expect stale-db                1 report-stale      --mode enforce --db-updated-at 2026-11-10T00:00:00Z --report "$tmp/clean.json"
expect no-db-timestamp         1 report-stale      --mode enforce --report "$tmp/clean.json"
expect warn-never-fails        0 fixable           --mode warn --db-updated-at "$DB" --report "$tmp/fix.json"
expect auto-before-enforce     0 fixable           --mode auto --db-updated-at "$DB" --report "$tmp/fix.json"
expect auto-after-enforce      1 fixable           --mode auto --now 2026-12-01T00:00:00Z --db-updated-at 2026-11-30T23:00:00Z --report "$tmp/fix.json"
((fails == 0)) || { echo "cve-gate: ${fails} failure(s)"; exit 1; }
echo "cve-gate: all outcomes OK"
