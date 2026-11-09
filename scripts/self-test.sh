#!/usr/bin/env bash
# estate-ci self-test. Runs in the estate-ci-ci job image (python:3.13-slim).
set -euo pipefail
export HOME="${HOME:-/tmp}"; [[ -w "$HOME" ]] || export HOME=/tmp
export PIP_DISABLE_PIP_VERSION_CHECK=1 PATH="$HOME/.local/bin:$PATH"
python3 -m pip install --quiet --user pyyaml==6.0.2 actionlint-py==1.7.12.25 shellcheck-py==0.11.0.1
python3 deploy/foundation/bin/lint-workflows.py --require-forgejo-notes .
python3 tests/lint/run.py
python3 tests/hot_guard/run.py >/dev/null
python3 tests/lint-images/run.py
# estate-ci enforces its own R4-tag-only now (callers from TAG_ONLY_ENFORCE_FROM, estate-ci#104).
python3 deploy/foundation/bin/lint-images.py --tags .
bash tests/sidecar_run/run.sh
actionlint -color
shellcheck deploy/foundation/bin/*.sh scripts/*.sh tests/sidecar_run/*.sh
python3 -m py_compile deploy/foundation/bin/hot-guard.py deploy/foundation/bin/pin-rewrite.py deploy/foundation/bin/lint-workflows.py deploy/foundation/bin/lint-images.py scripts/render_runners.py
python3 scripts/render_runners.py --check
echo "self-test: OK"
