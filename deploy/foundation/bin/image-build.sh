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
readback="$("$bin/crane" digest --insecure "${ORIGIN}/${image}:${tag}")"
[[ "$readback" == "$digest" ]] || { echo "digest readback mismatch" >&2; exit 1; }
rm -f "$K_TAR"
ref="${PUBLIC}/${image}@${digest}"
echo "digest=${digest}"
echo "image-ref=${ref}"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  { echo "digest=${digest}"; echo "image-ref=${ref}"; } >> "$GITHUB_OUTPUT"
fi
