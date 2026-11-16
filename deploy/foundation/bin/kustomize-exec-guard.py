#!/usr/bin/env python3
"""Refuse Kustomize exec plugins other than KSOPS (estate-platform#91 stage 1).

Argo CD renders every Kustomize Application with --enable-alpha-plugins --enable-exec,
and the repo-server that runs those renders holds the age identity. So any exec
generator, transformer or validator in a delivered repository could read that
identity. This check is the interim control: the only exec plugin a repository
may declare is KSOPS.

    kustomize-exec-guard.py [ROOT]     # default ROOT is the current directory

The check refuses:
  1. any YAML document whose config.kubernetes.io/function (or legacy
     config.k8s.io/function) annotation declares `exec`, unless it is
     apiVersion viaduct.ai/v1, kind ksops, exec path `ksops`;
  2. any generators/transformers/validators entry in a kustomization (file or
     inline) that resolves to a non-builtin plugin config other than KSOPS. With
     --enable-alpha-plugins, kustomize looks such a config up as an exec plugin by
     its apiVersion/kind;
  3. any generators/transformers/validators entry that is a remote URL, which this
     check cannot read.
Builtin plugin configs (apiVersion `builtin`) and directories (kustomizations,
which this walk covers file by file) are allowed. A document that does not parse
is skipped: kustomize could not load it either.

Exit 0 when clean, 1 on a violation, 2 on usage error. Output names the file, the
document index and the reason, and never prints document content.
"""
from __future__ import annotations

import os
import re
import sys

import yaml

KUSTOMIZATION_NAMES = {"kustomization.yaml", "kustomization.yml", "Kustomization"}
FUNCTION_ANNOTATIONS = ("config.kubernetes.io/function", "config.k8s.io/function")
PLUGIN_FIELDS = ("generators", "transformers", "validators")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
REMOTE = re.compile(r"^(https?://|git::|ssh://|git@|github\.com/|gitlab\.com/)")


def is_ksops(doc: dict) -> bool:
    return doc.get("apiVersion") == "viaduct.ai/v1" and doc.get("kind") == "ksops"


def function_spec(doc: dict):
    annotations = ((doc.get("metadata") or {}).get("annotations") or {})
    for key in FUNCTION_ANNOTATIONS:
        if key in annotations:
            raw = annotations[key]
            try:
                spec = yaml.safe_load(raw) if isinstance(raw, str) else raw
            except yaml.YAMLError:
                return {"_unparsed": True}
            return spec if isinstance(spec, dict) else {"_unparsed": True}
    return None


def exec_violation(doc: dict) -> str | None:
    spec = function_spec(doc)
    if spec is None:
        return None
    if spec.get("_unparsed"):
        return "function annotation does not parse; refused"
    if "exec" not in spec:
        return None
    path = (spec.get("exec") or {}).get("path") if isinstance(spec.get("exec"), dict) else None
    if is_ksops(doc) and path == "ksops":
        return None
    return (f"exec function (apiVersion {doc.get('apiVersion')!r}, kind {doc.get('kind')!r}, "
            f"path {path!r}); only viaduct.ai/v1 ksops with exec path 'ksops' is allowed")


def plugin_violation(doc: dict) -> str | None:
    if doc.get("apiVersion") == "builtin" or is_ksops(doc):
        return None
    spec = function_spec(doc)
    if spec is not None and "exec" not in spec and not spec.get("_unparsed"):
        return None  # container/starlark function: not exec (and has no runtime in Argo)
    return (f"plugin config apiVersion {doc.get('apiVersion')!r}, kind {doc.get('kind')!r} "
            "is not builtin or ksops; kustomize would resolve it as an exec plugin")


def load_docs(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return [d for d in yaml.safe_load_all(fh) if isinstance(d, dict)]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []


def check_kustomization(path: str, doc: dict, root: str, problems: list[str]) -> None:
    base = os.path.dirname(path)
    rel = os.path.relpath(path, root)
    for field in PLUGIN_FIELDS:
        for i, entry in enumerate(doc.get(field) or []):
            where = f"{rel}: {field}[{i}]"
            if not isinstance(entry, str):
                problems.append(f"{where}: entry is not a string; refused")
                continue
            if "\n" in entry:  # inline plugin config
                try:
                    docs = [d for d in yaml.safe_load_all(entry) if isinstance(d, dict)]
                except yaml.YAMLError:
                    problems.append(f"{where}: inline config does not parse; refused")
                    continue
                for d in docs:
                    reason = exec_violation(d) or plugin_violation(d)
                    if reason:
                        problems.append(f"{where} (inline): {reason}")
                continue
            if REMOTE.match(entry.strip()):
                problems.append(f"{where}: remote plugin reference cannot be checked; refused")
                continue
            target = os.path.normpath(os.path.join(base, entry))
            if os.path.isdir(target):
                continue  # a kustomization; its files are checked by the walk
            for j, d in enumerate(load_docs(target)):
                reason = plugin_violation(d)
                if reason:
                    problems.append(f"{where} -> {os.path.relpath(target, root)}#{j}: {reason}")


def main(argv: list[str]) -> int:
    if len(argv) > 2 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    root = os.path.abspath(argv[1] if len(argv) == 2 else ".")
    if not os.path.isdir(root):
        print(f"kustomize-exec-guard: {root} is not a directory", file=sys.stderr)
        return 2
    problems: list[str] = []
    files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if not (name.endswith((".yaml", ".yml")) or name in KUSTOMIZATION_NAMES):
                continue
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or os.path.getsize(path) > 5 * 1024 * 1024:
                continue
            files += 1
            docs = load_docs(path)
            for i, doc in enumerate(docs):
                reason = exec_violation(doc)
                if reason:
                    problems.append(f"{os.path.relpath(path, root)}#{i}: {reason}")
                if name in KUSTOMIZATION_NAMES or doc.get("kind") in ("Kustomization", "Component"):
                    check_kustomization(path, doc, root, problems)
    for p in problems:
        print(f"kustomize-exec-guard: {p}", file=sys.stderr)
    print(f"kustomize-exec-guard: {files} YAML file(s), {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
