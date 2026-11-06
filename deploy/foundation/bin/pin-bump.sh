#!/usr/bin/env bash
# estate-ci: open a PR that pins new image digests in GitOps manifests.
#
#   pin-bump.sh --pin "<file> <image> <sha256:digest>" [--pin ...]
#               [--source-sha <built commit>] [--base main] [--checks-workflow ci.yml] [--auto-merge]
#
# Which build publishes: the newest SUCCESSFUL build whose revision is still an
# ancestor of <base>. A build is never dropped just because <base> moved while it
# ran (that is the usual case); it is skipped only when its revision is not on
# <base> at all, or when a pin from a strictly newer revision (a descendant of
# this one) is already on <base> or open as an estate-ci/pin-* branch. Every pin
# commit records "Estate-CI-Source: <sha>" so later runs can order themselves.
#
# Plain git for the branch and commit; forge-api.sh for the PR, the checks
# dispatch and auto-merge (the only forge-specific part). forge-api.sh uses
# FORGE_TOKEN when set: pin-bump.yml passes a vallery-pin-bot App token there,
# so the PR starts pull_request workflows normally. A job-token PR starts none,
# so --checks-workflow (fallback only) dispatches the checks on the pin branch.
# Run from the repository root with push access (actions/checkout's default
# persisted token).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pins="" base=main checks="" automerge=false source_sha=""
while (($#)); do
  case "$1" in
    --pin) pins+="$2"$'\n'; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    --source-sha) source_sha="$2"; shift 2 ;;
    --checks-workflow) checks="$2"; shift 2 ;;
    --auto-merge) automerge=true; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n "$pins" ]] || { echo "no --pin given" >&2; exit 2; }

[[ -n "$source_sha" ]] || source_sha="$(git rev-parse HEAD)"
git fetch -q origin "$base" '+refs/heads/estate-ci/pin-*:refs/remotes/origin/estate-ci/pin-*'
git cat-file -e "${source_sha}^{commit}" 2>/dev/null || git fetch -q origin "$source_sha"
if ! git merge-base --is-ancestor "$source_sha" "origin/$base"; then
  echo "source ${source_sha:0:12} is not on ${base}; not publishing"
  exit 0
fi
# Newest wins: skip only if a strictly newer revision already has a pin (merged or open).
newer=""
while read -r ref; do
  src="$(git log -1 --format=%B "$ref" | sed -n 's/^[*[:space:]]*Estate-CI-Source: \([0-9a-f]\{40\}\)[[:space:]]*$/\1/p' | head -1)"
  [[ -n "$src" && "$src" != "$source_sha" ]] || continue
  git cat-file -e "${src}^{commit}" 2>/dev/null || continue
  if git merge-base --is-ancestor "$source_sha" "$src"; then newer="$src ($ref)"; break; fi
done < <(git log --format=%H --grep='Estate-CI-Source: ' -n 20 "origin/$base"; \
         git for-each-ref --format='%(refname)' 'refs/remotes/origin/estate-ci/pin-*')
if [[ -n "$newer" ]]; then
  echo "a newer build is already pinned: ${newer}; not publishing ${source_sha:0:12}"
  exit 0
fi
git checkout -q -B "estate-ci-pin-work" "origin/$base"
out="$(mktemp)"
PINS="$pins" PIN_OUTPUT="$out" python3 "$here/pin-rewrite.py"
changed="$(sed -n 's/^changed=//p' "$out")"
rm -f "$out"
if [[ -z "$changed" ]]; then
  echo "pins already current; no PR"
  exit 0
fi

branch="estate-ci/pin-${source_sha:0:12}-${GITHUB_RUN_ID:-$(date +%s)}"
git -c user.name="estate-ci" -c user.email="estate-ci@users.noreply.github.com" \
  commit -qam "deploy: pin ${changed} (built from ${source_sha:0:12})" -m "Estate-CI-Source: ${source_sha}"
git push -q origin "HEAD:refs/heads/${branch}"
run_url="${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
body="Automated digest pin from ${run_url} (source commit ${source_sha}).

Pins: ${changed}

Argo CD delivers this after merge.

Estate-CI-Source: ${source_sha}"
read -r url number node_id < <("$here/forge-api.sh" create-pr "$base" "$branch" "deploy: pin ${changed}" "$body")
echo "pin PR: ${url}"
[[ -n "${GITHUB_OUTPUT:-}" ]] && echo "pr-url=${url}" >> "$GITHUB_OUTPUT"
if [[ -n "$checks" ]]; then
  "$here/forge-api.sh" dispatch "$checks" "$branch"
  echo "dispatched ${checks} on ${branch}"
fi
if $automerge; then
  "$here/forge-api.sh" automerge "$number" "$node_id"
  echo "auto-merge enabled"
fi
