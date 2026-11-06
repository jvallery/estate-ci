#!/usr/bin/env bash
# Operator check (and fix) of the fork pull-request policy for onboarded repos
# (estate-ci#5, ADR-0005). Run by an operator with repo-admin `gh` auth, not in CI.
#
#   scripts/fork-policy.sh check owner/repo...
#   scripts/fork-policy.sh apply owner/repo...
#
# Public repos: workflow runs from fork PRs need approval for ALL outside
# collaborators (approval_policy=all_external_contributors). Private repos: fork
# PR workflows must stay off (run_workflows_from_fork_pull_requests=false; this
# script only reports it). The runner guard (job-started-guard.sh) refuses fork
# PR jobs anyway; this is the forge-side layer.
# Forgejo: the equivalent is the per-repo "approve workflows from fork PRs"
# behaviour (on by default for non-collaborators); re-check it in the phase-2 spike.
set -euo pipefail
mode="${1:?check|apply}"; shift
[[ "$mode" == check || "$mode" == apply ]] || { echo "usage: $0 check|apply owner/repo..." >&2; exit 2; }
want=all_external_contributors
bad=0
for repo in "$@"; do
  private="$(gh api "repos/${repo}" -q .private)"
  if [[ "$private" == true ]]; then
    fork="$(gh api "repos/${repo}/actions/permissions/fork-pr-workflows-private-repos" \
      -q .run_workflows_from_fork_pull_requests 2>/dev/null || echo unknown)"
    if [[ "$fork" == false || "$fork" == unknown ]]; then
      echo "${repo}: private; fork PR workflows: ${fork/unknown/off (owner default)} OK"
    else
      echo "${repo}: private; fork PR workflows ENABLED (turn off in repo settings)"; bad=1
    fi
    continue
  fi
  have="$(gh api "repos/${repo}/actions/permissions/fork-pr-contributor-approval" -q .approval_policy)"
  if [[ "$have" == "$want" ]]; then
    echo "${repo}: public; approval_policy=${have} OK"
  elif [[ "$mode" == apply ]]; then
    gh api -X PUT "repos/${repo}/actions/permissions/fork-pr-contributor-approval" \
      -f approval_policy="$want" >/dev/null
    for _ in 1 2 3 4 5; do # the read side is eventually consistent
      now="$(gh api "repos/${repo}/actions/permissions/fork-pr-contributor-approval" -q .approval_policy)"
      [[ "$now" == "$want" ]] && break
      sleep 2
    done
    echo "${repo}: public; approval_policy ${have} -> ${now}"
    [[ "$now" == "$want" ]] || bad=1
  else
    echo "${repo}: public; approval_policy=${have} (want ${want})"; bad=1
  fi
done
exit "$bad"
