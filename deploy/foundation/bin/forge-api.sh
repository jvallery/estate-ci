#!/usr/bin/env bash
# estate-ci: the ONE place that talks to the forge's HTTP API.
#
#   forge-api.sh create-pr <base> <head> <title> <body>   -> prints "<html_url> <number> <node_id>"
#   forge-api.sh dispatch  <workflow-file> <ref>
#   forge-api.sh automerge <number> <node_id>
#
# FORGE        github (default) | forgejo   (Gitea/Forgejo API v1)
# FORGE_API    API base; default $GITHUB_API_URL (GitHub) or $GITHUB_SERVER_URL/api/v1 (Forgejo)
# FORGE_REPO   owner/name; default $GITHUB_REPOSITORY
# FORGE_TOKEN  token; default $GITHUB_TOKEN (the job token on both forges)
set -euo pipefail
forge="${FORGE:-github}"
repo="${FORGE_REPO:-${GITHUB_REPOSITORY:?}}"
token="${FORGE_TOKEN:-${GITHUB_TOKEN:?FORGE_TOKEN or GITHUB_TOKEN is required}}"
case "$forge" in
  github) api="${FORGE_API:-${GITHUB_API_URL:-https://api.github.com}}" ;;
  forgejo) api="${FORGE_API:-${GITHUB_SERVER_URL:?}/api/v1}" ;;
  *) echo "unknown FORGE $forge" >&2; exit 2 ;;
esac
json() { python3 -c 'import json,sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }
call() { # method path [json]
  local auth="Authorization: Bearer ${token}"
  [[ "$forge" == forgejo ]] && auth="Authorization: token ${token}"
  curl -fsS -X "$1" -H "$auth" -H "Accept: application/json" -H "Content-Type: application/json" \
    "${api}$2" ${3:+-d "$3"}
}
cmd="${1:?command}"; shift
case "$cmd" in
  create-pr)
    resp="$(call POST "/repos/${repo}/pulls" "$(json base "$1" head "$2" title "$3" body "$4")")"
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["html_url"], d["number"], d.get("node_id", ""))' <<< "$resp"
    ;;
  dispatch)
    call POST "/repos/${repo}/actions/workflows/$1/dispatches" "$(json ref "$2")" >/dev/null
    ;;
  automerge)
    if [[ "$forge" == github ]]; then
      # shellcheck disable=SC2016 # GraphQL variables, not shell
      q='mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:SQUASH}){clientMutationId}}'
      body="$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "variables": {"id": sys.argv[2]}}))' "$q" "$2")"
      curl -fsS -X POST -H "Authorization: Bearer ${token}" "${GITHUB_GRAPHQL_URL:-https://api.github.com/graphql}" -d "$body" >/dev/null
    else
      call POST "/repos/${repo}/pulls/$1/merge" '{"Do":"squash","merge_when_checks_succeed":true}' >/dev/null
    fi
    ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
