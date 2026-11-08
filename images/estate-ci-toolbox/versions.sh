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
# monitoring-stack Static Validation archives (Dockerfile): exact path and sha256.
archive() { # tool version basename sha256
  f="/opt/estate-ci-tools/$1/$2/$3"
  check "$1-archive" "$2 $4" "$2 $(sha256sum "$f" 2>/dev/null | awk '{print $1}')"
}
archive kustomize         v5.4.2 kustomize_v5.4.2_linux_amd64.tar.gz           881c6e9007c7ea2b9ecc214d13f4cdd1f837635dcf4db49ce4479898f7d911a3
archive kubeconform       v0.6.7 kubeconform-linux-amd64.tar.gz                95f14e87aa28c09d5941f11bd024c1d02fdc0303ccaa23f61cef67bc92619d73
archive prometheus        3.13.5 prometheus-3.13.5.linux-amd64.tar.gz          0388822ace519c5a3a8ef556a541ce1b536e21d872f82deded465bd27a5a0699
archive alertmanager      0.34.1 alertmanager-0.34.1.linux-amd64.tar.gz        265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8
archive tempo             3.0.2  tempo_3.0.2_linux_amd64.tar.gz                d2b95c01774c81c4494c4162e2f3511699a66b46506c96028745f5d4f0a78086
archive blackbox_exporter 0.29.1 blackbox_exporter-0.29.1.linux-amd64.tar.gz   b82b81839e6317e84fdd367f46d7ddd3b773fa09cbe2e3d5633a59171d741e76
check runner-uid  1001        "$(id -u runner)"
check runner-sh   ok          "$(test -x /home/runner/run.sh && echo ok)"
exit "$bad"
