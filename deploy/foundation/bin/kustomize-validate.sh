#!/usr/bin/env bash
# The one estate kustomize-validate entry point (estate-ci#156): build each kustomize
# root and schema-validate every rendered object with kubeconform -strict. Lane repos
# call it through .github/workflows/kustomize-validate.yml or as
# $ESTATE_CI/kustomize-validate.sh, instead of carrying their own copy. Needs
# kustomize and kubeconform on PATH (the estate-ci toolbox image ships both; a job
# sidecar gets them from the estate-ci-tools init container).
#
#   kustomize-validate.sh [options] ROOT...
#     --kubernetes-version V   kubeconform -kubernetes-version (default: kubeconform's)
#     --schema-location L      extra kubeconform -schema-location (repeatable); the
#                              default location is always tried first
#     --ignore-missing-schemas pass -ignore-missing-schemas (CRDs with no schema)
#     --forbid-secrets         fail if a render contains a kind: Secret (only counts
#                              are printed, never object content)
#   The reusable workflow passes the same inputs through the environment (into a job
#   sidecar): KV_ROOTS (whitespace-separated), KV_KUBERNETES_VERSION,
#   KV_SCHEMA_LOCATIONS (newline-separated), KV_IGNORE_MISSING_SCHEMAS=true and
#   KV_FORBID_SECRETS=true. Arguments add to them.
#
# Rendered output goes to a private temp dir that is removed on exit; it is never
# printed, so a render that does carry a Secret cannot leak its values into the log.
set -euo pipefail

usage() { sed -n '2,20p' "$0" >&2; exit 2; }

k8s_version="${KV_KUBERNETES_VERSION:-}"
ignore_missing=0; [[ "${KV_IGNORE_MISSING_SCHEMAS:-}" != true ]] || ignore_missing=1
forbid_secrets=0; [[ "${KV_FORBID_SECRETS:-}" != true ]] || forbid_secrets=1
schemas=()
while IFS= read -r s; do [[ -z "$s" ]] || schemas+=("$s"); done <<< "${KV_SCHEMA_LOCATIONS:-}"
roots=()
read -r -a roots <<< "${KV_ROOTS:-}"
while (($#)); do
  case "$1" in
    --kubernetes-version) [[ $# -ge 2 ]] || usage; k8s_version="$2"; shift 2 ;;
    --schema-location) [[ $# -ge 2 ]] || usage; schemas+=("$2"); shift 2 ;;
    --ignore-missing-schemas) ignore_missing=1; shift ;;
    --forbid-secrets) forbid_secrets=1; shift ;;
    -h|--help) usage ;;
    --*) echo "kustomize-validate: unknown option $1" >&2; usage ;;
    *) roots+=("$1"); shift ;;
  esac
done
((${#roots[@]})) || usage

for tool in kustomize kubeconform; do
  if ! command -v "$tool" >/dev/null; then
    echo "kustomize-validate: $tool is required (estate-ci toolbox image)" >&2
    exit 2
  fi
done

kc=(-strict -summary -output text)
[[ -z "$k8s_version" ]] || kc+=(-kubernetes-version "$k8s_version")
((ignore_missing == 0)) || kc+=(-ignore-missing-schemas)
if ((${#schemas[@]})); then
  kc+=(-schema-location default)
  for s in "${schemas[@]}"; do kc+=(-schema-location "$s"); done
fi

umask 077
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failed=0
for root in "${roots[@]}"; do
  out="$work/render.yaml"
  echo "== kustomize-validate: $root"
  if ! kustomize build "$root" > "$out"; then
    echo "kustomize-validate: kustomize build $root failed" >&2
    failed=1
    continue
  fi
  objects="$(grep -c '^kind:' "$out" || true)"
  if ((forbid_secrets)); then
    secrets="$(grep -cx 'kind: Secret' "$out" || true)"
    if ((secrets > 0)); then
      echo "kustomize-validate: $root renders $secrets Secret object(s); Secrets must not be in a plain render" >&2
      failed=1
      continue
    fi
  fi
  if ! kubeconform "${kc[@]}" - < "$out"; then
    echo "kustomize-validate: $root failed schema validation" >&2
    failed=1
    continue
  fi
  echo "kustomize-validate: $root OK ($objects objects)"
done
exit "$failed"
