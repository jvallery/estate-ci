#!/bin/sh
# Print the estate-ci-toolbox tool versions; with --assert, fail on any drift
# from the pins in the Dockerfile (README.md lists the same table).
set -eu
assert=false; [ "${1:-}" = --assert ] && assert=true
bad=0
check() { # name expected actual
  printf '%-12s %s\n' "$1" "$3"
  if $assert && [ "$2" != "$3" ]; then echo "  ! $1: want $2" >&2; bad=1; fi
}
first() { sed -n 1p; }
check python3     3.12        "$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
check pyyaml      ok          "$(python3 -c 'import yaml; print("ok")')"
check pip         ok          "$(python3 -m pip --version >/dev/null && echo ok)"
check uv          0.13.5      "$(uv --version | awk '{print $2}')"
check node        v24.22.1    "$(node --version)"
check npm         ok          "$(npm --version >/dev/null && echo ok)"
check corepack    ok          "$(corepack --version >/dev/null && echo ok)"
check ruby        3.2         "$(ruby -e 'print RUBY_VERSION.split(".")[0,2].join(".")')"
check php         8.3         "$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;')"
check kubectl     v1.35.10    "$(kubectl version --client -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')"
check kustomize   v5.8.3      "$(kustomize version)"
check helm        v3.22.1     "$(helm version --template '{{.Version}}')"
check kubeconform v0.8.0      "$(kubeconform -v)"
check yq          v4.54.1     "$(yq --version | awk '{print $NF}')"
check jq          ok          "$(jq --version >/dev/null && echo ok)"
check crane       v0.22.1     "v$(crane version)"
check gitleaks    8.30.1      "$(gitleaks version)"
check shellcheck  0.11.0      "$(shellcheck --version | sed -n 's/^version: //p')"
check actionlint  1.7.12      "$(actionlint --version | first)"
check gh          2.103.3     "$(gh --version | first | awk '{print $3}')"
check git         ok          "$(git --version >/dev/null && echo ok)"
check g++         ok          "$(g++ --version >/dev/null && echo ok)"
check sqlite3     ok          "$(sqlite3 --version >/dev/null && echo ok)"
check runner-uid  1001        "$(id -u runner)"
check runner-sh   ok          "$(test -x /home/runner/run.sh && echo ok)"
exit "$bad"
