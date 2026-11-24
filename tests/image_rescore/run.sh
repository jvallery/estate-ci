#!/usr/bin/env bash
# D-1344: image-build.sh --rescore re-scores an already pushed digest against the current R26 rows
# after a cve-gate block. crane, trivy and oras are stubs; the rows come from a local git repo in
# place of git-mirror. Controls (enforce forced, then warn):
#   1 fixable Critical, no row                -> block, no digest output (no pin)
#   2 same digest, row for it                 -> pass excepted, digest output (pin job receives it)
#   3 row for another digest / another owner  -> block, no digest output
# and the refusals: digest not in the registry, digest built by another repository, --rescore
# without --push, malformed digest.
set -euo pipefail
build="$(cd "$(dirname "$0")/../.." && pwd)/deploy/foundation/bin/image-build.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
D="sha256:$(printf 'a%.0s' {1..64})"
OTHER="sha256:$(printf 'b%.0s' {1..64})"
OWNER="jvallery/example"
IMAGE="jvallery/example-app"

# Workspace: a checkout of the calling repository.
mkdir -p "$tmp/ws"; (cd "$tmp/ws" && git init -q && echo 'FROM scratch' > Dockerfile && git add Dockerfile \
  && git -c user.email=t@t -c user.name=t commit -qm init && git remote add origin "https://github.com/${OWNER}.git")

# Stubs in the runner's tool directory (image-build.sh downloads a tool only when it is missing).
bin="$tmp/rt/estate-ci-bin"; mkdir -p "$bin" "$tmp/auth"; echo '{}' > "$tmp/auth/config.json"
cat > "$bin/crane" <<'EOF'
#!/usr/bin/env bash
# crane config --insecure <origin>/<image>@<digest>: only STUB_DIGEST exists; labels from STUB_SOURCE.
[[ "$1" == config ]] || exit 1
[[ "${@: -1}" == *"/${STUB_IMAGE}@${STUB_DIGEST}" ]] || { echo "MANIFEST_UNKNOWN" >&2; exit 1; }
jq -n --arg s "$STUB_SOURCE" '{config: {Labels: {"org.opencontainers.image.source": $s, "org.opencontainers.image.revision": "0123456789ab"}}}'
EOF
cat > "$bin/trivy" <<'EOF'
#!/usr/bin/env bash
# DB downloads succeed; version reports a fresh DB; a scan writes STUB_REPORT to --output.
args=" $* "
[[ "$args" == *" version "* ]] && { jq -n --arg u "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{VulnerabilityDB: {UpdatedAt: $u}}'; exit 0; }
out=""; while (($#)); do [[ "$1" == --output ]] && out="$2"; shift; done
[[ -n "$out" && "$args" == *" --scanners vuln "* ]] && cp "$STUB_REPORT" "$out"
exit 0
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/oras"
chmod +x "$bin/crane" "$bin/trivy" "$bin/oras"

# Trivy report for D: one fixable Critical.
jq -n --arg d "$D" '{Metadata: {RepoDigests: ["registry-origin:5000/jvallery/example-app@" + $d]},
  Results: [{Target: "x", Vulnerabilities: [{VulnerabilityID: "CVE-2026-0001", Severity: "CRITICAL", FixedVersion: "1.2.3"}]}]}' > "$tmp/report.json"

rows() { # digest owner -> the rows repository's single commit
  rm -rf "$tmp/exc"; mkdir -p "$tmp/exc/security"
  jq -n --arg d "$1" --arg o "$2" '{exceptions: [{id: "t", register: "R26", owner: $o, manifest_digest: $d,
    cves: ["CVE-2026-0001"], expires_at: "2099-01-01T00:00:00Z"}]}' > "$tmp/exc/security/rows.json"
  (cd "$tmp/exc" && git init -q && git add . && git -c user.email=t@t -c user.name=t commit -qm rows)
}

fails=0
run() { # name want-rc want-text want-digest-output(yes|no) mode [extra image-build args...]
  local name="$1" want="$2" text="$3" emit="$4" mode="$5"; shift 5
  local out rc=0
  : > "$tmp/gh_output"
  out="$(cd "$tmp/ws" && env RUNNER_TEMP="$tmp/rt" ESTATE_CI_ZOT_AUTH_DIR="$tmp/auth" GITHUB_OUTPUT="$tmp/gh_output" \
    GITHUB_REPOSITORY="$OWNER" CVE_GATE_MODE="$mode" ESTATE_CI_EXCEPTIONS_GIT="$tmp/exc" ESTATE_CI_EXCEPTIONS_PATH=security/rows.json \
    STUB_DIGEST="$D" STUB_IMAGE="$IMAGE" STUB_SOURCE="${STUB_SOURCE:-https://github.com/${OWNER}}" STUB_REPORT="$tmp/report.json" \
    bash "$build" --image "$IMAGE" "$@" 2>&1)" || rc=$?
  local got=no; grep -qx "digest=${D}" "$tmp/gh_output" && got=yes
  if [[ "$rc" != "$want" || "$out" != *"$text"* || "$got" != "$emit" ]]; then
    echo "FAIL ${name}: rc=${rc} (want ${want}), digest output ${got} (want ${emit}), output: ${out}"; fails=$((fails + 1))
  else
    echo "ok   ${name}: rc=${rc} '${text}' digest-output=${got}"
  fi
}
R=(--push --rescore "$D")
rows "$OTHER" "$OWNER"
run no-row-blocks             1 "verdict=block reason=fixable"  no  enforce "${R[@]}"
rows "$D" "$OWNER"
run row-for-digest-passes     0 "verdict=pass reason=excepted"  yes enforce "${R[@]}"
rows "$OTHER" "$OWNER"
run row-other-digest-blocks   1 "verdict=block reason=fixable"  no  enforce "${R[@]}"
rows "$D" "jvallery/someone-else"
run row-other-owner-blocks    1 "verdict=block reason=fixable"  no  enforce "${R[@]}"
run warn-mode-never-fails     0 "verdict=block reason=fixable"  yes warn    "${R[@]}"
rows "$D" "$OWNER"
run digest-not-in-registry    2 "is not in the registry"        no  enforce --push --rescore "$OTHER"
STUB_SOURCE="https://github.com/jvallery/intruder" \
run built-by-other-repo       2 "was not built by"              no  enforce "${R[@]}"
run rescore-needs-push        2 "--rescore needs --push"        no  enforce --rescore "$D"
run rescore-bad-digest        2 "invalid --rescore"             no  enforce --push --rescore sha256:abc
((fails == 0)) || { echo "image-rescore: ${fails} failure(s)"; exit 1; }
echo "image-rescore: all outcomes OK"
