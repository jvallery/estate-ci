#!/bin/bash
# estate-ci ACTIONS_RUNNER_HOOK_JOB_STARTED guard.
#
# Runs in the runner container before every job. A non-zero exit fails the job
# before any workflow step runs. The script lives in a ConfigMap, so a pull
# request cannot change it.
#
#   CI_EXPECTED_REPOSITORY  owner/name this scale set serves, or 'Owner/*' for an
#                           org-level set that serves every repo of that owner
#   CI_RUNNER_PROFILE       ci | pg | build | buildcheck
#   CI_BUILD_REF            ref build runners serve (default refs/heads/main)
set -euo pipefail
fail() { echo "estate-ci guard: $*" >&2; exit 1; }

repo="${GITHUB_REPOSITORY:-}"
event="${GITHUB_EVENT_NAME:-}"
expected="${CI_EXPECTED_REPOSITORY:-}"
profile="${CI_RUNNER_PROFILE:-ci}"

[[ -n "$repo" && -n "$event" ]] || fail "missing GITHUB_REPOSITORY/GITHUB_EVENT_NAME"
[[ -n "$expected" ]] || fail "CI_EXPECTED_REPOSITORY is not set"
if [[ "$expected" == */\* ]]; then
  owner="${expected%/*}"
  [[ "${repo%%/*}" == "$owner" || "${repo,,}" == "${owner,,}/"* ]] || fail "scale set serves ${owner}/*, job is from ${repo}"
else
  [[ "${repo,,}" == "${expected,,}" ]] || fail "scale set serves ${expected}, job is from ${repo}"
fi

# shellcheck disable=SC2012 # fixed runner path, no odd names
node="$(ls -d /home/runner/externals/node*/bin/node 2>/dev/null | sort -V | tail -1)"
[[ -x "$node" ]] || fail "runner node binary not found"
head_repo=""
if [[ -n "${GITHUB_EVENT_PATH:-}" && -r "${GITHUB_EVENT_PATH}" ]]; then
  head_repo="$("$node" -e '
    const e = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    const pr = e.pull_request || {};
    process.stdout.write(((pr.head || {}).repo || {}).full_name || "");
  ' "$GITHUB_EVENT_PATH")"
fi

case "$event" in
  pull_request_target|workflow_run)
    fail "event ${event} runs with base-repo privileges and is not allowed on self-hosted runners" ;;
  pull_request|pull_request_review)
    # Fork pull requests never run on estate runners.
    [[ -n "$head_repo" && "${head_repo,,}" == "${repo,,}" ]] || fail "fork pull request (${head_repo:-unknown}) refused" ;;
esac

if [[ "$profile" == "build" ]]; then
  # build sets mount the repo's zot push credential: default-branch push or
  # dispatch only. PR builds use the credential-free "<repo>-buildcheck" set.
  build_ref="${CI_BUILD_REF:-refs/heads/main}"
  case "$event" in
    push|workflow_dispatch) ;;
    *) fail "event ${event} may not use a build runner (use <repo>-buildcheck for PR builds)" ;;
  esac
  [[ "${GITHUB_REF:-}" == "$build_ref" ]] || fail "build runners serve ${build_ref} only, not ${GITHUB_REF:-unknown}"
fi
echo "estate-ci guard: ${repo} ${event} ${GITHUB_REF:-} allowed on profile ${profile}"
