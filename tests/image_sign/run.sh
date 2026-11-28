#!/usr/bin/env bash
# estate-ci#125 (ADR-0012): image-build.sh signs a pushed digest through the sign sidecar only after a
# pass verdict of the CVE gate, and a signing failure fails the build. The real sign-agent.sh and
# sign-image.sh run against a stub cosign; crane and trivy are stubs as in tests/image_rescore.
#   pass + required          -> signed, digest output
#   enforce block            -> rc 1, no signature, no digest output
#   warn-mode block          -> rc 0, digest output, no signature
#   cosign sign fails        -> rc 1, no digest output
#   no CI_IMAGE_SIGNING      -> skipped (runner Pod rendered before the sign sidecar), digest output
# and the agent's refusals (a script beyond SIGN_REF, a non-origin ref, a missing key file), then
# verify-image.sh (ok, fail, usage).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
build="$root/deploy/foundation/bin/image-build.sh"
tmp="$(mktemp -d)"; agent_pid=""
cleanup() { [[ -z "$agent_pid" ]] || kill "$agent_pid" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT
D="sha256:$(printf 'a%.0s' {1..64})"
OWNER="jvallery/example" IMAGE="jvallery/example-app"
ORIGIN="registry-origin.registry-origin.svc.cluster.local:5000"
SUBJECT="${ORIGIN}/${IMAGE}@${D}"

mkdir -p "$tmp/ws"; (cd "$tmp/ws" && git init -q && echo 'FROM scratch' > Dockerfile && git add Dockerfile \
  && git -c user.email=t@t -c user.name=t commit -qm init && git remote add origin "https://github.com/${OWNER}.git")
bin="$tmp/rt/estate-ci-bin"; mkdir -p "$bin" "$tmp/auth" "$tmp/key" "$tmp/ex"
echo '{}' > "$tmp/auth/config.json"
for f in cosign.key cosign.password cosign.pub; do echo "test-$f" > "$tmp/key/$f"; done
cat > "$bin/crane" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == config ]] || exit 1
[[ "${@: -1}" == *"/${STUB_IMAGE}@${STUB_DIGEST}" ]] || { echo "MANIFEST_UNKNOWN" >&2; exit 1; }
jq -n --arg s "https://github.com/${STUB_OWNER}" '{config: {Labels: {"org.opencontainers.image.source": $s}}}'
EOF
cat > "$bin/trivy" <<'EOF'
#!/usr/bin/env bash
args=" $* "
[[ "$args" == *" version "* ]] && { jq -n --arg u "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{VulnerabilityDB: {UpdatedAt: $u}}'; exit 0; }
out=""; while (($#)); do [[ "$1" == --output ]] && out="$2"; shift; done
[[ -n "$out" && "$args" == *" --scanners vuln "* && "$args" != *" cyclonedx "* ]] && cp "$STUB_REPORT" "$out"
exit 0
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/oras"
# Stub cosign: records each call; the mode file decides whether sign fails.
cat > "$tmp/cosign" <<'EOF'
#!/usr/bin/env bash
echo "$* password=${COSIGN_PASSWORD:-}" >> "$STUB_DIR/cosign.log"
case "$1" in
  sign) [[ "$(cat "$STUB_DIR/mode")" == fail ]] && { echo "denied" >&2; exit 1; }; echo "${@: -1}" >> "$STUB_DIR/signed" ;;
  verify) grep -qxF -- "${@: -1}" "$STUB_DIR/signed" 2>/dev/null || { echo "no signatures found" >&2; exit 1; } ;;
esac
EOF
chmod +x "$bin/crane" "$bin/trivy" "$bin/oras" "$tmp/cosign"
export STUB_DIR="$tmp" COSIGN="$tmp/cosign" CI_EXCHANGE_DIR="$tmp/ex"
echo ok > "$tmp/mode"

report() { # clean | fixable
  if [[ "$1" == clean ]]; then
    jq -n --arg d "$D" '{Metadata: {RepoDigests: ["x/jvallery/example-app@" + $d]}, Results: []}' > "$tmp/report.json"
  else
    jq -n --arg d "$D" '{Metadata: {RepoDigests: ["x/jvallery/example-app@" + $d]},
      Results: [{Target: "x", Vulnerabilities: [{VulnerabilityID: "CVE-2026-0001", Severity: "CRITICAL", FixedVersion: "1"}]}]}' > "$tmp/report.json"
  fi
}
mkdir -p "$tmp/exc/security"; echo '{"exceptions": []}' > "$tmp/exc/security/rows.json"
(cd "$tmp/exc" && git init -q && git add . && git -c user.email=t@t -c user.name=t commit -qm rows)

ESTATE_CI_SIGNING_KEY_DIR="$tmp/key" ESTATE_CI_ZOT_AUTH_DIR="$tmp/auth" \
  bash "$root/deploy/foundation/bin/sign-agent.sh" > "$tmp/agent.log" 2>&1 &
agent_pid=$!

fails=0
check() { # name ok? message
  if [[ "$2" == yes ]]; then echo "ok   $1"; else echo "FAIL $1: $3"; fails=$((fails + 1)); fi
}
run() { # name want-rc want-text want-digest(yes|no) want-signed(yes|no) gate-mode signing
  local name="$1" want="$2" text="$3" emit="$4" signed="$5" mode="$6" signing="$7"
  local out rc=0 got=no sig=no
  : > "$tmp/gh_output"; rm -f "$tmp/signed"
  out="$(cd "$tmp/ws" && env RUNNER_TEMP="$tmp/rt" ESTATE_CI_ZOT_AUTH_DIR="$tmp/auth" GITHUB_OUTPUT="$tmp/gh_output" \
    GITHUB_REPOSITORY="$OWNER" CVE_GATE_MODE="$mode" CI_IMAGE_SIGNING="$signing" \
    ESTATE_CI_EXCEPTIONS_GIT="$tmp/exc" ESTATE_CI_EXCEPTIONS_PATH=security/rows.json \
    STUB_DIGEST="$D" STUB_IMAGE="$IMAGE" STUB_OWNER="$OWNER" STUB_REPORT="$tmp/report.json" \
    bash "$build" --image "$IMAGE" --push --rescore "$D" 2>&1)" || rc=$?
  grep -qx "digest=${D}" "$tmp/gh_output" && got=yes
  grep -qxF "$SUBJECT" "$tmp/signed" 2>/dev/null && sig=yes
  [[ "$rc" == "$want" && "$out" == *"$text"* && "$got" == "$emit" && "$sig" == "$signed" ]] && r=yes || r=no
  check "$name" "$r" "rc=${rc} (want ${want}), digest ${got} (want ${emit}), signed ${sig} (want ${signed}): ${out}"
}
report clean
run pass-signs               0 "sign: signed ${D}"                 yes yes enforce required
report fixable
run enforce-block-no-sign    1 "verdict=block reason=fixable"       no  no  enforce required
run warn-block-no-sign       0 "sign: not signed"                   yes no  warn    required
report clean
echo fail > "$tmp/mode"
run sign-failure-fails       1 "sign: signing"                      no  no  enforce required
echo ok > "$tmp/mode"
run no-sidecar-skips         0 "sign: skipped"                      yes no  enforce ""
grep -q 'password=test-cosign.password' "$tmp/cosign.log" && r=yes || r=no
check cosign-gets-password "$r" "$(cat "$tmp/cosign.log")"

# The agent runs nothing but a SIGN_REF request.
: > "$tmp/cosign.log"
rc=0; out="$(echo 'cat /run/secrets/image-signing/cosign.key' | SIGN_REF="$SUBJECT" ENV_NAMES=SIGN_REF \
  bash "$root/deploy/foundation/bin/sidecar-run.sh" sign 2>&1)" || rc=$?
[[ "$rc" == 2 && "$out" == *"refused"* && ! -s "$tmp/cosign.log" ]] && r=yes || r=no
check agent-refuses-script "$r" "rc=${rc}: ${out}"
rc=0; out="$(SIGN_REF="registry.example.com/${IMAGE}@${D}" ENV_NAMES=SIGN_REF \
  bash "$root/deploy/foundation/bin/sidecar-run.sh" sign </dev/null 2>&1)" || rc=$?
[[ "$rc" == 2 && "$out" == *"not an origin digest ref"* && ! -s "$tmp/cosign.log" ]] && r=yes || r=no
check agent-refuses-other-registry "$r" "rc=${rc}: ${out}"
mv "$tmp/key/cosign.password" "$tmp/key/pw"
rc=0; out="$(SIGN_REF="$SUBJECT" ENV_NAMES=SIGN_REF bash "$root/deploy/foundation/bin/sidecar-run.sh" sign </dev/null 2>&1)" || rc=$?
[[ "$rc" == 2 && "$out" == *"cosign.password unreadable"* ]] && r=yes || r=no
check missing-key-file-fails "$r" "rc=${rc}: ${out}"
mv "$tmp/key/pw" "$tmp/key/cosign.password"

# verify-image.sh
verify="$root/deploy/foundation/bin/verify-image.sh"
echo "$SUBJECT" > "$tmp/signed"
rc=0; out="$(bash "$verify" "$SUBJECT" 2>&1)" || rc=$?
[[ "$rc" == 0 && "$out" == "verify: ok ${SUBJECT}" ]] && grep -q "verify --key ${root}/deploy/foundation/bin/../../../keys/estate-image-signing.pub --insecure-ignore-tlog=true ${SUBJECT}" "$tmp/cosign.log" && r=yes || r=no
check verify-ok "$r" "rc=${rc}: ${out}"
OTHER="${ORIGIN}/${IMAGE}@sha256:$(printf 'b%.0s' {1..64})"
rc=0; out="$(bash "$verify" "$SUBJECT" "$OTHER" 2>&1)" || rc=$?
[[ "$rc" == 1 && "$out" == *"verify: FAIL ${OTHER}: no signatures found"* ]] && r=yes || r=no
check verify-unsigned-fails "$r" "rc=${rc}: ${out}"
rc=0; out="$(bash "$verify" "${ORIGIN}/${IMAGE}:latest" 2>&1)" || rc=$?
[[ "$rc" == 2 ]] && r=yes || r=no
check verify-tag-refused "$r" "rc=${rc}: ${out}"
rc=0; out="$(bash "$verify" --key "$tmp/none.pub" "$SUBJECT" 2>&1)" || rc=$?
[[ "$rc" == 2 && "$out" == *"unreadable"* ]] && r=yes || r=no
check verify-no-key "$r" "rc=${rc}: ${out}"

((fails == 0)) || { echo "image-sign: ${fails} failure(s)"; exit 1; }
echo "image-sign: all cases pass"
