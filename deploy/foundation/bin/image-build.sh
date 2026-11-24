#!/usr/bin/env bash
# estate-ci: build one image with the Pod's Kaniko sidecar; optionally push it to zot by digest.
#
#   image-build.sh --image jvallery/weather-app [--dockerfile Dockerfile] [--context .]
#                  [--target stage] [--build-arg NAME=value ...] [--push]
#
# Kaniko builds to a tarball with --no-push and holds only the registry PULL
# credential. With --push (only on a "<repo>-build" set, whose job guard admits
# default-branch push/dispatch only) the runner container pushes the tarball with
# crane using the repo's own zot principal mounted at /run/secrets/zot-push.
# Dockerfile RUN steps never see that credential.
#
# Output: prints "digest=sha256:..." and "image-ref=registry.vallery.net/<image>@sha256:..."
# and appends them to $GITHUB_OUTPUT when set (Forgejo Actions honours it too).
# Run from the repository root (the shared workspace).
set -euo pipefail
image="" dockerfile="Dockerfile" context="." target="" push=false
build_args=()
while (($#)); do
  case "$1" in
    --image) image="$2"; shift 2 ;;
    --dockerfile) dockerfile="$2"; shift 2 ;;
    --context) context="$2"; shift 2 ;;
    --target) target="$2"; shift 2 ;;
    --build-arg) build_args+=("$2"); shift 2 ;;
    --push) push=true; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
ORIGIN="${ESTATE_CI_REGISTRY_ORIGIN:-registry-origin.registry-origin.svc.cluster.local:5000}"
PUBLIC="${ESTATE_CI_REGISTRY_PUBLIC:-registry.vallery.net}"
CRANE_VERSION=v0.22.1
CRANE_SHA256=0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0
# estate-ci#127 (SA-9): Trivy scans every pushed digest; cve-gate.sh decides (warn, then enforce from its
# ENFORCE_AFTER). Same Trivy release and DB sources as the estate image scan (agents estate-security).
TRIVY_VERSION=0.76.1
TRIVY_SHA256=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0
# estate-ci#125 (ADR-0012 step 3): oras attaches the CycloneDX SBOM to the pushed digest as an OCI referrer.
ORAS_VERSION=1.3.6
ORAS_SHA256=66fb0ad7206f3f6fa77ed5b552049d364ccf4b50b1684554f731f76302b52ca0
TRIVY_DB_REPOSITORIES="${ESTATE_CI_TRIVY_DB:-mirror.gcr.io/aquasec/trivy-db:2,ghcr.io/aquasecurity/trivy-db:2}"
TRIVY_JAVA_DB_REPOSITORIES="${ESTATE_CI_TRIVY_JAVA_DB:-mirror.gcr.io/aquasec/trivy-java-db:1,ghcr.io/aquasecurity/trivy-java-db:1}"
# R26 findings exceptions, read anonymously from the in-cluster git mirror (no credential, STD-CI-15): the
# register the estate image scan reads (estate-platform, moved from agents under ep#693; D-1344 M1).
EXCEPTIONS_GIT="${ESTATE_CI_EXCEPTIONS_GIT:-http://git-mirror.git-mirror.svc.cluster.local/jvallery/estate-platform.git}"
EXCEPTIONS_PATH="${ESTATE_CI_EXCEPTIONS_PATH:-security/estate-security-report/findings-exceptions.json}"

[[ "$image" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+$ ]] || { echo "invalid --image" >&2; exit 2; }
[[ "$target" =~ ^[A-Za-z0-9_.-]{0,128}$ ]] || { echo "invalid --target" >&2; exit 2; }
root="$(pwd -P)"
ctx="$(realpath -m "$root/$context")"
df="$(realpath -m "$root/$dockerfile")"
[[ "$ctx" == "$root"* && "$df" == "$root"* && -d "$ctx" && -f "$df" ]] || { echo "context/dockerfile missing or outside the workspace" >&2; exit 2; }
for a in "${build_args[@]+"${build_args[@]}"}"; do
  [[ "$a" =~ ^[A-Za-z_][A-Za-z0-9_]*=[^$'\n\r']*$ ]] || { echo "invalid --build-arg" >&2; exit 2; }
done
revision="$(git rev-parse HEAD)"
source_url="$(git config --get remote.origin.url | sed -E 's#^https://[^@/]+@#https://#; s#\.git$##')"
short="${revision:0:12}"
if $push; then
  [[ -r /run/secrets/zot-push/config.json ]] || { echo "--push needs the repo's zot push credential (run on a <repo>-build set)" >&2; exit 2; }
fi

out_dir="${CI_EXCHANGE_DIR:-/exchange}/kaniko-out"
mkdir -p "$out_dir"
export K_CONTEXT="$ctx" K_DOCKERFILE="$df" K_TARGET="$target"
export K_DEST="${PUBLIC}/${image}:gha-${short}" K_TAR="${out_dir}/${short}-${RANDOM}.tar"
export K_SOURCE="$source_url" K_REVISION="$revision"
K_BUILD_ARGS="$(printf '%s\n' "${build_args[@]+"${build_args[@]}"}")"
export K_BUILD_ARGS
ENV_NAMES="K_CONTEXT K_DOCKERFILE K_TARGET K_DEST K_TAR K_SOURCE K_REVISION K_BUILD_ARGS" \
  "$(dirname "$0")/sidecar-run.sh" kaniko <<'KANIKO'
set -- --context="dir://${K_CONTEXT}" --dockerfile="${K_DOCKERFILE}" \
  --destination="${K_DEST}" --no-push --tar-path="${K_TAR}" \
  --snapshot-mode=full --compressed-caching=false --cleanup \
  --label=org.opencontainers.image.source="${K_SOURCE}" \
  --label=org.opencontainers.image.revision="${K_REVISION}"
[ -z "${K_TARGET}" ] || set -- "$@" --target="${K_TARGET}"
while IFS= read -r line; do
  [ -z "$line" ] || set -- "$@" --build-arg="$line"
done <<ARGS
${K_BUILD_ARGS}
ARGS
/kaniko/executor "$@"
chmod 0644 "${K_TAR}"
KANIKO
echo "built ${image} (no push yet)"

if ! $push; then
  rm -f "$K_TAR"
  echo "build-only: --push not set"
  exit 0
fi

bin="${RUNNER_TEMP:-/tmp}/estate-ci-bin"
if [[ ! -x "$bin/crane" ]]; then
  mkdir -p "$bin"
  curl -fsSL --retry 3 -o "$bin/crane.tgz" \
    "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz"
  echo "${CRANE_SHA256}  $bin/crane.tgz" | sha256sum -c - >/dev/null
  tar -xzf "$bin/crane.tgz" -C "$bin" crane
  rm -f "$bin/crane.tgz"
fi
export DOCKER_CONFIG=/run/secrets/zot-push
# gha- prefix: native CI pushes sha-<12> for the same commit during a parallel run.
tag="gha-${short}"
pushed="$("$bin/crane" push --insecure "$K_TAR" "${ORIGIN}/${image}:${tag}")"
digest="${pushed##*@}"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "unexpected push output" >&2; exit 1; }
# Post-push digest read, retried only on an origin outage (storage-infra#1959: a 45 s writer
# readiness flap failed a complete push on "connection refused"). Up to 6 tries 15 s apart, only
# for connection refused/reset or a 5xx; a 4xx or any other error fails at once. Every retry is logged.
readback="" rb_err="$(mktemp)"
for attempt in 1 2 3 4 5 6; do
  if readback="$("$bin/crane" digest --insecure "${ORIGIN}/${image}:${tag}" 2>"$rb_err")"; then
    break
  fi
  if ((attempt < 6)) && ! grep -qE 'status code 4[0-9]{2}' "$rb_err" \
     && grep -qE 'connection refused|connection reset|status code 5[0-9]{2}' "$rb_err"; then
    echo "digest readback retry ${attempt}/5 in 15 s: $(tail -n 1 "$rb_err")" >&2
    sleep 15
    continue
  fi
  cat "$rb_err" >&2; rm -f "$rb_err"
  echo "digest readback failed after ${attempt} attempt(s)" >&2; exit 1
done
rm -f "$rb_err"
[[ "$readback" == "$digest" ]] || { echo "digest readback mismatch" >&2; exit 1; }
rm -f "$K_TAR"
ref="${PUBLIC}/${image}@${digest}"

# --- critical-CVE gate (estate-ci#127). The digest stays in zot when blocked, so an owner can add an
# exact-digest R26 row and re-run. Any scanner or fetch failure leaves no report, which the gate treats
# as report-missing (pass in warn mode, block in enforce mode).
gate_dir="${RUNNER_TEMP:-/tmp}/cve-gate-${short}"
rm -rf "$gate_dir"; mkdir -p "$gate_dir"
if [[ ! -x "$bin/trivy" ]]; then
  curl -fsSL --retry 3 -o "$bin/trivy.tgz" \
    "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    && echo "${TRIVY_SHA256}  $bin/trivy.tgz" | sha256sum -c - >/dev/null \
    && tar -xzf "$bin/trivy.tgz" -C "$bin" trivy || echo "cve-gate: trivy download failed" >&2
  rm -f "$bin/trivy.tgz"
fi
db_updated=""
if [[ -x "$bin/trivy" ]]; then
  t=("$bin/trivy" --cache-dir "$gate_dir/cache" --quiet)
  if "${t[@]}" image --download-db-only --db-repository "$TRIVY_DB_REPOSITORIES" >&2 \
     && "${t[@]}" image --download-java-db-only --java-db-repository "$TRIVY_JAVA_DB_REPOSITORIES" >&2; then
    db_updated="$("${t[@]}" version --format json | jq -r '.VulnerabilityDB.UpdatedAt // empty')"
    DOCKER_CONFIG=/run/secrets/zot-push "${t[@]}" image --skip-db-update --skip-java-db-update --insecure \
      --scanners vuln --timeout 15m --format json --output "$gate_dir/report.json" \
      "${ORIGIN}/${image}@${digest}" >&2 || { echo "cve-gate: trivy scan failed" >&2; rm -f "$gate_dir/report.json"; }
  else
    echo "cve-gate: trivy DB download failed" >&2
  fi
fi
if ! git clone -q --depth 1 --filter=blob:none --no-checkout "$EXCEPTIONS_GIT" "$gate_dir/exc" 2>/dev/null \
   || ! git -C "$gate_dir/exc" show "HEAD:${EXCEPTIONS_PATH}" > "$gate_dir/exceptions.json" 2>/dev/null; then
  echo "cve-gate: findings exceptions unavailable; evaluating with no rows" >&2; : > "$gate_dir/exceptions.json"
fi
echo "cve-gate: scanner trivy ${TRIVY_VERSION}, db ${db_updated:-unknown}, report sha256 $(sha256sum "$gate_dir/report.json" 2>/dev/null | cut -c1-64)"
owner="${GITHUB_REPOSITORY:-$(sed -E 's#^https?://[^/]+/##' <<<"$source_url")}"
gate_rc=0
bash "$(dirname "$0")/cve-gate.sh" --report "$gate_dir/report.json" --digest "$digest" --owner "$owner" \
  --exceptions "$gate_dir/exceptions.json" --db-updated-at "$db_updated" || gate_rc=$?
if ((gate_rc == 0)); then
  # SBOM after the gate passes, so a blocked digest gets none; warn mode, never fails the build.
  if [[ ! -x "$bin/oras" ]]; then
    curl -fsSL --retry 3 -o "$bin/oras.tgz" \
      "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz" \
      && echo "${ORAS_SHA256}  $bin/oras.tgz" | sha256sum -c - >/dev/null \
      && tar -xzf "$bin/oras.tgz" -C "$bin" oras || echo "sbom: oras download failed" >&2
    rm -f "$bin/oras.tgz"
  fi
  bash "$(dirname "$0")/sbom-attach.sh" --trivy "$bin/trivy" --oras "$bin/oras" --cache "$gate_dir/cache" \
    --registry-config /run/secrets/zot-push/config.json --ref "${ORIGIN}/${image}@${digest}" || true
fi
rm -rf "$gate_dir"
((gate_rc == 0)) || { echo "cve-gate blocked ${ref}: no digest output, so no pin PR" >&2; exit 1; }
echo "digest=${digest}"
echo "image-ref=${ref}"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  { echo "digest=${digest}"; echo "image-ref=${ref}"; } >> "$GITHUB_OUTPUT"
fi
