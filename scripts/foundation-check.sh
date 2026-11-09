#!/usr/bin/env bash
# estate-ci#111 (STD-IAC-17): build and schema-validate deploy/foundation, the one
# Argo-delivered path in this repo (Application estate-ci-foundation auto-syncs main
# and its estate-ci-scripts ConfigMap reaches every runner). Runs on the
# estate-ci-ci-toolbox set, whose image ships kustomize and kubeconform.
# A missing generator file, a broken resource or an invalid object fails here, before merge.
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in kustomize kubeconform; do
  if ! command -v "$tool" >/dev/null; then
    echo "foundation-check: $tool is required (estate-ci toolbox image)" >&2
    exit 2
  fi
done
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
kustomize build deploy/foundation > "$out"
# Every file under deploy/foundation/bin must ship in a generated ConfigMap; a script
# left out of configMapGenerator would be missing from /opt/estate-ci on every runner.
missing=0
for f in deploy/foundation/bin/*; do
  name="$(basename "$f")"
  if ! awk -v key="  ${name}:" 'index($0, key) == 1 { found = 1 } END { exit !found }' "$out"; then
    echo "foundation-check: $f is not in any configMapGenerator (deploy/foundation/kustomization.yaml)" >&2
    missing=1
  fi
done
[[ "$missing" == 0 ]]
# Schemas: kubeconform's default location (yannh/kubernetes-json-schema over public 443,
# allowed by runner-egress). Every kind here is core Kubernetes, so no CRD schema is needed.
kubeconform -strict -summary -output text - < "$out"
echo "foundation-check: OK ($(grep -c '^kind:' "$out") objects)"
