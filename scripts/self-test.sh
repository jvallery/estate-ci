#!/usr/bin/env bash
# estate-ci self-test. Runs in the estate-ci-ci job image (python:3.13-slim).
set -euo pipefail
export HOME="${HOME:-/tmp}"; [[ -w "$HOME" ]] || export HOME=/tmp
export PIP_DISABLE_PIP_VERSION_CHECK=1 PATH="$HOME/.local/bin:$PATH"
python3 -m pip install --quiet --user pyyaml==6.0.2 actionlint-py==1.7.12.25 shellcheck-py==0.11.0.1
python3 deploy/foundation/bin/lint-workflows.py --require-forgejo-notes .
python3 tests/lint/run.py
python3 tests/lint_allow/run.py
python3 tests/hot_guard/run.py >/dev/null
python3 tests/runner_budget/run.py
python3 tests/runner_slots/run.py >/dev/null
# estate-ci#115: closure check for runner-set Secrets (fails on a missing name).
python3 tests/runner_secrets/run.py
python3 tests/lint-images/run.py
# estate-ci#114: every image this repo delivers is a registry.vallery.net digest (mirrors.txt first).
python3 tests/origin_images/run.py
python3 tests/version_register/run.py
# estate-ci enforces its own R4-tag-only now (callers from TAG_ONLY_ENFORCE_FROM, estate-ci#104).
python3 deploy/foundation/bin/lint-images.py --tags .
bash tests/sidecar_run/run.sh
actionlint -color
shellcheck deploy/foundation/bin/*.sh scripts/*.sh tests/sidecar_run/*.sh tests/kustomize_validate/*.sh
python3 -m py_compile deploy/foundation/bin/hot-guard.py deploy/foundation/bin/runner-slots.py deploy/foundation/bin/pin-rewrite.py deploy/foundation/bin/lint-workflows.py deploy/foundation/bin/lint-images.py deploy/foundation/bin/build-plan.py deploy/foundation/bin/lint-version-register.py tests/build_plan/run.py scripts/render_runners.py scripts/check-runner-secrets.py
python3 scripts/render_runners.py --check
echo "self-test: OK"
