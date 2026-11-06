#!/usr/bin/env bash
# estate-ci: open a PR that pins new image digests in GitOps manifests.
#
#   pin-bump.sh --pin "<file> <image> <sha256:digest>" [--pin ...]
#               [--base main] [--checks-workflow ci.yml] [--auto-merge]
#
# Plain git for the branch and commit; forge-api.sh for the PR, the checks
# dispatch and auto-merge (the only forge-specific part). The job token cannot
# trigger workflows by itself on GitHub, so the checks workflow is dispatched on
# the pin branch; its check runs land on the PR head. Run from the repository
# root with push access (actions/checkout's default persisted token).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pins="" base=main checks="" automerge=false
while (($#)); do
  case "$1" in
    --pin) pins+="$2"$'\n'; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    --checks-workflow) checks="$2"; shift 2 ;;
    --auto-merge) automerge=true; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n "$pins" ]] || { echo "no --pin given" >&2; exit 2; }

source_sha="$(git rev-parse HEAD)"
git fetch -q origin "$base"
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
  commit -qam "deploy: pin ${changed} (built from ${source_sha:0:12})"
git push -q origin "HEAD:refs/heads/${branch}"
run_url="${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
body="Automated digest pin from ${run_url} (source commit ${source_sha}).

Pins: ${changed}

Argo CD delivers this after merge."
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
