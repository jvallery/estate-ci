#!/usr/bin/env bash
# estate-ci: fail if any workflow names a forge-hosted runner label (estate rule R1).
#
#   check-runner-labels.sh [repo-root]
#
# Every job must run on an ARC scale set ("<repo>-ci", "<repo>-pg", "<repo>-build",
# or an org-level set). Hosted labels (ubuntu-*, windows-*, macos-*, and the
# bare "self-hosted"/"linux" selectors that could match a non-estate runner)
# are refused. Also refuses `uses:` of anything but actions/checkout, local
# (./) actions and jvallery/estate-ci reusable workflows (R2).
set -euo pipefail
root="${1:-.}"
shopt -s nullglob
files=("$root"/.github/workflows/*.y*ml "$root"/.forgejo/workflows/*.y*ml "$root"/.gitea/workflows/*.y*ml)
((${#files[@]})) || { echo "no workflow files"; exit 0; }
bad=0
hosted='(ubuntu-[a-z0-9.-]+|windows-[a-z0-9.-]+|macos-[a-z0-9.-]+|self-hosted|linux|x64|arm64)'
for f in "${files[@]}"; do
  n=0
  while IFS= read -r line; do
    n=$((n + 1))
    code="${line%%#*}"
    if [[ "$code" =~ runs-on: ]]; then
      value="${code#*runs-on:}"
      if [[ "$value" =~ (^|[^A-Za-z0-9_.-])${hosted}([^A-Za-z0-9_.-]|$) ]]; then
        echo "${f}:${n}: hosted or generic runner label: ${value# }" >&2
        bad=1
      fi
    fi
    if [[ "$code" =~ uses:[[:space:]]*([^[:space:]]+) ]]; then
      ref="${BASH_REMATCH[1]}"
      ref="${ref%\"}"; ref="${ref#\"}"; ref="${ref%\'}"; ref="${ref#\'}"
      case "$ref" in
        ./*|actions/checkout@*|jvallery/estate-ci/.github/workflows/*@*) ;;
        *) echo "${f}:${n}: action/workflow not allowed by R2: ${ref}" >&2; bad=1 ;;
      esac
    fi
  done < "$f"
done
if ((bad)); then
  echo "check-runner-labels: FAILED (every job runs on an ARC set; only actions/checkout and estate-ci workflows)" >&2
  exit 1
fi
echo "check-runner-labels: ${#files[@]} workflow file(s) OK"
