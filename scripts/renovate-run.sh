#!/usr/bin/env bash
# estate-ci#261 (STD-OPS-02, DR-51): the estate version detector's run loop.
#
#   scripts/renovate-run.sh run     one Renovate pass per repository in the active
#                                   batches of renovate/repositories.json
#   scripts/renovate-run.sh probe   for every non-exception repository, report only
#                                   whether vallery-pin-bot is installed (no token,
#                                   no Renovate)
#
# Runs in the runner container of estate-ci-ci-renovate; Renovate itself runs in
# the job sidecar (renovate/renovate image) through $ESTATE_CI/sidecar-run.sh.
# Each pass mints a vallery-pin-bot installation token for that ONE repository
# (D-52, $ESTATE_CI/app-token.py: contents + pull_requests write, 1 h) and revokes
# it when the pass ends. Needs APP_CLIENT_ID and APP_PRIVATE_KEY in the env; the
# App key never enters the sidecar. Renovate only opens PRs: it never merges,
# pins digests or touches Argo (renovate/global.json).
set -euo pipefail
mode="${1:-run}"
here="$(cd "$(dirname "$0")/.." && pwd)"
: "${ESTATE_CI:?ESTATE_CI is not set (estate-ci runner pod)}"
: "${APP_CLIENT_ID:?}" "${APP_PRIVATE_KEY:?}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
list() { python3 "$here/scripts/renovate-repos.py" "$1"; }

python3 "$here/scripts/renovate-repos.py" check

if [[ "$mode" == probe ]]; then
  mapfile -t repos < <(list all)
  printf '| repository | vallery-pin-bot |\n|---|---|\n' >> "$summary"
  missing=0
  for repo in "${repos[@]}"; do
    rc=0; python3 "$here/scripts/renovate-repos.py" installed "$repo" || rc=$?
    case "$rc" in
      0) state=installed ;;
      3) state=not-installed; missing=$((missing + 1)) ;;
      *) state="lookup-error-$rc"; missing=$((missing + 1)) ;;
    esac
    echo "renovate-probe: $repo: $state"
    printf '| %s | %s |\n' "$repo" "$state" >> "$summary"
  done
  echo "renovate-probe: ${#repos[@]} repositories, $missing without an installation"
  exit 0
fi
[[ "$mode" == run ]] || { echo "usage: renovate-run.sh run|probe" >&2; exit 2; }

mapfile -t repos < <(list active)
echo "renovate: ${#repos[@]} repositories in active batches: ${repos[*]}"
printf '| repository | result |\n|---|---|\n' >> "$summary"

RENOVATE_TOKEN=""
revoke() {
  if [[ -n "$RENOVATE_TOKEN" ]]; then
    APP_TOKEN="$RENOVATE_TOKEN" python3 "$ESTATE_CI/app-token.py" --revoke || true
    RENOVATE_TOKEN=""
  fi
}
trap revoke EXIT

export RENOVATE_PLATFORM=github RENOVATE_CONFIG_FILE="$here/renovate/global.json"
export LOG_LEVEL="${RENOVATE_LOG_LEVEL:-info}"
failed=0 uncovered=0 scanned=0
for repo in "${repos[@]}"; do
  echo "::group::renovate $repo"
  rc=0; python3 "$here/scripts/renovate-repos.py" installed "$repo" || rc=$?
  if (( rc != 0 )); then
    result="not-covered: vallery-pin-bot not installed (rc $rc)"
    echo "::warning title=renovate::$repo $result"
    uncovered=$((uncovered + 1))
  else
    RENOVATE_TOKEN="$(python3 "$ESTATE_CI/app-token.py" "$repo")"
    echo "::add-mask::$RENOVATE_TOKEN"
    export RENOVATE_TOKEN RENOVATE_REPOSITORIES="$repo"
    rc=0
    ENV_NAMES="RENOVATE_TOKEN RENOVATE_PLATFORM RENOVATE_REPOSITORIES RENOVATE_CONFIG_FILE LOG_LEVEL" \
      TIMEOUT_MINUTES=20 "$ESTATE_CI/sidecar-run.sh" job <<< 'cd /tmp && /usr/local/sbin/renovate-entrypoint.sh renovate' || rc=$?
    revoke
    if (( rc == 0 )); then
      result=scanned; scanned=$((scanned + 1))
    else
      result="renovate failed (exit $rc)"; failed=$((failed + 1))
      echo "::error title=renovate::$repo $result"
    fi
  fi
  echo "::endgroup::"
  echo "renovate: $repo: $result"
  printf '| %s | %s |\n' "$repo" "$result" >> "$summary"
done
echo "renovate: ${#repos[@]} repositories: $scanned scanned, $uncovered not covered, $failed failed"
(( failed == 0 ))
