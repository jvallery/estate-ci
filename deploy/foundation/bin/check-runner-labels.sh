#!/usr/bin/env bash
# Superseded by lint-workflows.py (R1-R3 workflow lint, estate-ci#5); kept so
# existing callers keep working.
#   check-runner-labels.sh [repo-root]
set -euo pipefail
exec python3 "$(dirname "$0")/lint-workflows.py" "${1:-.}"
