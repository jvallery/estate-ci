#!/usr/bin/env bash
# estate-ci: digest-preserving image mirrors into the estate registry (crane copy, no Kaniko).
#
#   mirror.sh [--file mirrors.txt] [--prefix jvallery/<repo>/] [--apply]
#
# The file lists one mirror per line ("#" starts a comment):
#
#   <upstream-ref>@sha256:<digest>  <dest-repository>
#   ghcr.io/actions/actions-runner:2.338.2@sha256:759a...  jvallery/estate-ci/mirror/ghcr.io/actions/actions-runner
#
# Default (check, no credential, PRs): every line is well formed, every
# destination is under --prefix (default "<owner>/<repo>/" from GITHUB_REPOSITORY,
# lower-cased), there are no duplicates, and each upstream digest resolves.
# --apply (only on a "<repo>-build" set: default-branch push/dispatch, with the
# repo's zot push credential at /run/secrets/zot-push): copy each missing digest
# to the zot origin as <dest>:pinned-<digest12>, then read the digest back. The
# copy is byte-for-byte, so the pinned digest is the upstream digest: an image
# index stays an index (all platforms); list a platform child digest to mirror
# one platform. Already-present digests are skipped, so re-runs are cheap.
# Output per line: "mirrored|present <dest>@<digest>" and the pin to use,
# registry.vallery.net/<dest>@<digest>.
#
# Portability: plain bash + crane; runs the same under Forgejo Actions or by hand.
set -euo pipefail
file="mirrors.txt" prefix="" apply=false
while (($#)); do
  case "$1" in
    --file) file="$2"; shift 2 ;;
    --prefix) prefix="$2"; shift 2 ;;
    --apply) apply=true; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$prefix" ]]; then
  repo="${GITHUB_REPOSITORY:?--prefix or GITHUB_REPOSITORY is required}"
  prefix="${repo,,}/"
fi
[[ -f "$file" ]] || { echo "mirror list ${file} not found" >&2; exit 2; }
ORIGIN="${ESTATE_CI_REGISTRY_ORIGIN:-registry-origin.registry-origin.svc.cluster.local:5000}"
PUBLIC="${ESTATE_CI_REGISTRY_PUBLIC:-registry.vallery.net}"
CRANE_VERSION=v0.22.1
CRANE_SHA256=0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0

crane="$(command -v crane || true)"
if [[ -z "$crane" ]]; then
  bin="${RUNNER_TEMP:-/tmp}/estate-ci-bin"
  crane="$bin/crane"
  if [[ ! -x "$crane" ]]; then
    mkdir -p "$bin"
    curl -fsSL --retry 3 -o "$bin/crane.tgz" \
      "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz"
    echo "${CRANE_SHA256}  $bin/crane.tgz" | sha256sum -c - >/dev/null
    tar -xzf "$bin/crane.tgz" -C "$bin" crane
    rm -f "$bin/crane.tgz"
  fi
fi

if $apply; then
  [[ -r /run/secrets/zot-push/config.json ]] || { echo "--apply needs the repo's zot push credential (run on a <repo>-build set)" >&2; exit 2; }
  export DOCKER_CONFIG=/run/secrets/zot-push
else
  # Anonymous upstream reads; never pick up an operator's credential helper.
  DOCKER_CONFIG="$(mktemp -d)"; export DOCKER_CONFIG
  echo '{}' > "$DOCKER_CONFIG/config.json"
fi

name_re='^[a-z0-9]+([._-]+[a-z0-9]+)*(/[a-z0-9]+([._-]+[a-z0-9]+)*)+$'
declare -A seen=()
bad=0 n=0
while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="${raw%%#*}"
  read -r src dest extra <<< "$line" || true
  [[ -n "${src:-}" ]] || continue
  n=$((n + 1))
  if [[ -n "${extra:-}" || -z "${dest:-}" ]]; then
    echo "line ${n}: want '<upstream>@sha256:<digest> <dest-repository>': ${raw}" >&2; bad=1; continue
  fi
  digest="${src##*@}"
  if [[ "$src" != *@* || ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "line ${n}: ${src}: upstream must be pinned by @sha256 digest" >&2; bad=1; continue
  fi
  if [[ ! "$dest" =~ $name_re || "$dest" != "$prefix"* ]]; then
    echo "line ${n}: ${dest}: destination must be a lower-case repository under ${prefix}" >&2; bad=1; continue
  fi
  key="${dest}@${digest}"
  if [[ -n "${seen[$key]:-}" ]]; then
    echo "line ${n}: duplicate ${key}" >&2; bad=1; continue
  fi
  seen[$key]=1
  if ! got="$("$crane" digest "$src" 2>/dev/null)" || [[ "$got" != "$digest" ]]; then
    echo "line ${n}: ${src}: upstream digest does not resolve" >&2; bad=1; continue
  fi
  if ! $apply; then
    echo "ok ${src} -> ${PUBLIC}/${dest}@${digest}"
    continue
  fi
  target="${ORIGIN}/${dest}"
  if [[ "$("$crane" digest --insecure "${target}@${digest}" 2>/dev/null || true)" == "$digest" ]]; then
    "$crane" tag --insecure "${target}@${digest}" "pinned-${digest:7:12}" >/dev/null
    echo "present ${PUBLIC}/${dest}@${digest}"
    continue
  fi
  "$crane" copy --insecure "$src" "${target}:pinned-${digest:7:12}"
  readback="$("$crane" digest --insecure "${target}:pinned-${digest:7:12}")"
  if [[ "$readback" != "$digest" ]]; then
    echo "line ${n}: ${dest}: readback ${readback} != ${digest}" >&2; bad=1; continue
  fi
  echo "mirrored ${PUBLIC}/${dest}@${digest}"
done < "$file"
((n)) || echo "no mirrors listed in ${file}"
if ((bad)); then
  echo "mirror: FAILED" >&2
  exit 1
fi
echo "mirror: ${n} entr$([[ $n == 1 ]] && echo y || echo ies) OK ($($apply && echo apply || echo check))"
