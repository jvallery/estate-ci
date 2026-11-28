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
# ($ESTATE_CI/app-token.py: contents + pull_requests write, 1 h) and revokes
# it when the pass ends. Needs APP_CLIENT_ID and APP_PRIVATE_KEY in the env; the
# App key never enters the sidecar. Renovate only opens PRs: it never merges,
# pins digests or touches Argo (renovate/global.json).
#
# PR budget: one run opens at most RENOVATE_RUN_PR_BUDGET (default 10) new pull
# requests across every repository, so activating a batch cannot flood the
# runners. Each pass gets prHourlyLimit = min(2, what is left) and is charged
# what it opened (renovate-repos.py opened); once the budget is spent, the
# remaining repositories still get their pass, as dryRun=lookup (detection
# only, no branches or pull requests), and the next run continues.
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
budget="${RENOVATE_RUN_PR_BUDGET:-10}"
run_start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for repo in "${repos[@]}"; do
  echo "::group::renovate $repo"
  rc=0; python3 "$here/scripts/renovate-repos.py" installed "$repo" || rc=$?
  if (( rc != 0 )); then
    result="not-covered: vallery-pin-bot not installed (rc $rc)"
    echo "::warning title=renovate::$repo $result"
    uncovered=$((uncovered + 1))
  else
    # Renovate's initRepo GraphQL reads repository.issues: without issues:read every
    # repository except estate-ci failed 'platform-unknown-error' (2026-11-18 09:22Z run).
    RENOVATE_TOKEN="$(APP_TOKEN_EXTRA_PERMISSIONS=issues=read python3 "$ESTATE_CI/app-token.py" "$repo")"
    echo "::add-mask::$RENOVATE_TOKEN"
    export RENOVATE_TOKEN RENOVATE_REPOSITORIES="$repo"
    names="RENOVATE_TOKEN RENOVATE_PLATFORM RENOVATE_REPOSITORIES RENOVATE_CONFIG_FILE LOG_LEVEL"
    if (( budget > 0 )); then
      export RENOVATE_PR_HOURLY_LIMIT=$(( budget < 2 ? budget : 2 ))
      names+=" RENOVATE_PR_HOURLY_LIMIT"; pass=scanned
    else
      export RENOVATE_DRY_RUN=lookup
      names+=" RENOVATE_DRY_RUN"; pass="scanned (lookup only: run PR budget spent)"
    fi
    rc=0
    ENV_NAMES="$names" \
      TIMEOUT_MINUTES=20 "$ESTATE_CI/sidecar-run.sh" job <<< 'cd /tmp && /usr/local/sbin/renovate-entrypoint.sh renovate' || rc=$?
    if (( budget > 0 )); then
      # Charge what the pass opened; if the count cannot be read, charge the whole pass limit.
      n="$(python3 "$here/scripts/renovate-repos.py" opened "$repo" "$run_start")" || n="$RENOVATE_PR_HOURLY_LIMIT"
      budget=$(( budget > n ? budget - n : 0 ))
      echo "renovate: $repo opened $n pull request(s); run PR budget left: $budget"
    fi
    unset RENOVATE_PR_HOURLY_LIMIT RENOVATE_DRY_RUN
    revoke
    if (( rc == 0 )); then
      result="$pass"; scanned=$((scanned + 1))
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
