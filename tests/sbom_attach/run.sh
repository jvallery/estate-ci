#!/usr/bin/env bash
# estate-ci#125: deploy/foundation/bin/sbom-attach.sh with stub trivy/oras. Every case must exit 0
# (warn mode); the log line decides attached vs warn.
set -euo pipefail
script="$(cd "$(dirname "$0")/../.." && pwd)/deploy/foundation/bin/sbom-attach.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
D="sha256:$(printf 'a%.0s' {1..64})"; R="sha256:$(printf 'b%.0s' {1..64})"
REF="registry-origin:5000/jvallery/example@$D"
mkdir -p "$tmp/cache" "$tmp/cfg"; echo '{}' > "$tmp/cfg/config.json"
cat > "$tmp/trivy" <<'T'
#!/usr/bin/env bash
out=""; while (($#)); do [[ "$1" == --output ]] && out="$2"; shift; done
case "${STUB_TRIVY:-ok}" in
  ok) echo '{"bomFormat":"CycloneDX","components":[{"name":"a"},{"name":"b"}]}' > "$out" ;;
  junk) echo '{"x":1}' > "$out" ;;
  fail) exit 1 ;;
esac
T
cat > "$tmp/oras" <<'O'
#!/usr/bin/env bash
case "$1" in
  attach) [[ "${STUB_ORAS:-ok}" == fail ]] && { echo "denied" >&2; exit 1; }; echo "$STUB_REF" ;;
  discover) if [[ "${STUB_ORAS:-ok}" == missing ]]; then echo '{"referrers":[]}'; else echo "{\"referrers\":[{\"digest\":\"$STUB_REF\"}]}"; fi ;;
esac
O
chmod +x "$tmp/trivy" "$tmp/oras"
export STUB_REF="$R"
fails=0
expect() { # name want-substring env...
  local name="$1" want="$2"; shift 2
  local out rc=0
  out="$(env "$@" bash "$script" --trivy "$tmp/trivy" --oras "$tmp/oras" --cache "$tmp/cache" \
    --registry-config "$tmp/cfg/config.json" --ref "$REF" 2>&1)" || rc=$?
  if [[ "$rc" != 0 || "$out" != *"$want"* ]]; then echo "FAIL $name rc=$rc: $out"; fails=$((fails+1)); else echo "ok   $name"; fi
}
expect attached "sbom: attached $R to $D (cyclonedx, 2 components" STUB_TRIVY=ok
expect trivy-fails "sbom: warn trivy cyclonedx failed" STUB_TRIVY=fail
expect not-cyclonedx "sbom: warn trivy output is not CycloneDX" STUB_TRIVY=junk
expect attach-denied "sbom: warn oras attach failed: denied" STUB_ORAS=fail
expect not-listed "sbom: warn referrer $R not listed" STUB_ORAS=missing
expect bad-attach-output "sbom: warn unexpected attach output" STUB_REF=nope
out="$(bash "$script" --trivy "$tmp/trivy" --oras "$tmp/oras" --cache "$tmp/cache" --registry-config "$tmp/cfg/config.json" --ref registry-origin:5000/x:latest)"
[[ "$out" == *"invalid --ref"* ]] && echo "ok   bad-ref" || { echo "FAIL bad-ref: $out"; fails=$((fails+1)); }
out="$(bash "$script" --trivy /nonexistent --oras "$tmp/oras" --cache "$tmp/cache" --registry-config "$tmp/cfg/config.json" --ref "$REF")"
[[ "$out" == *"trivy unavailable"* ]] && echo "ok   no-trivy" || { echo "FAIL no-trivy: $out"; fails=$((fails+1)); }
((fails == 0)) && echo "sbom-attach: all cases pass" || { echo "sbom-attach: $fails failure(s)"; exit 1; }
