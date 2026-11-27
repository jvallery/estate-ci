#!/usr/bin/env python3
"""Check the child Applications a nested Argo CD root reads from this repository.

A nested root (for example network-infra-root in jvallery/estate-platform) is an
Application that reads one directory of this repository, non-recursively, and
creates every Application declared there. The root tracks `main`, so a merged
child file is what the next root sync applies. Nothing at runtime restricts a
child's name or its project: the root's AppProject admits any Application in
namespace argocd. This check is therefore where a child's identity is fixed
(jvallery/estate-platform#1553).

    owner-root-children.py --config ci/owner-root-children.yaml [--repo-root DIR]

The config lives in the calling repository, outside every root's directory
(Argo CD would try to apply it otherwise):

    schema_version: 1
    roots:
      - root: network-infra-root          # the nested root's name
        path: argocd                      # the directory it reads
        repository: jvallery/network-infra  # the only repository a child may source
        applications:                     # every manifest file and what it declares
          - {file: pihole.yaml, name: pihole, project: network-infra-edge}
        deviations:                       # optional; the list only shrinks
          - manifest: pihole.yaml
            application: pihole
            deviations: [retry-missing]
            reason: Why this Application does not meet STD-IAC-08 yet.

Fixed rules, which no deviation row can waive:
  - every manifest (*.yaml, *.yml, *.json) directly under the path is listed in
    `applications`, and every listed file exists;
  - every document is an argoproj.io/v1alpha1 Application in namespace argocd,
    with exactly the listed names and projects (no ApplicationSet, no other kind);
  - every source reads `repository`, from the in-cluster Git mirror or from
    github.com, at `main` or at a 40-character commit;
  - the destination is the local cluster;
  - `syncPolicy.automated.prune` is not true and no sync option sets Prune=true.

STD-IAC-08 rules, which a deviation row with a reason can record (same ids as
gitops/application-spec-baseline.yaml in jvallery/estate-platform):
  server-side-apply-missing, create-namespace-true, retry-missing,
  finalizer-reason-missing, respect-ignore-differences-missing,
  ignore-differences[N]:unnamed, :whole-data, :statefulset-identity, :writer-missing.
A deviation that is not recorded fails; a recorded one that no longer occurs
fails as stale.

Exit 0 when clean, 1 on a finding, 2 on a usage or config error. Output names the
file, the Application and the rule, and never prints document content.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

MIRROR = "http://git-mirror.git-mirror.svc.cluster.local/"
GITHUB = "https://github.com/"
IN_CLUSTER = "https://kubernetes.default.svc"
MANIFEST_SUFFIXES = (".yaml", ".yml", ".json")
REVISION = re.compile(r"\A(main|[0-9a-f]{40})\Z")
REPOSITORY = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
FINALIZER = "resources-finalizer.argocd.argoproj.io"
FINALIZER_REASON = "estate.vallery.net/finalizer-reason"
CREATE_NAMESPACE_REASON = "estate.vallery.net/create-namespace-reason"
WRITERS = "estate.vallery.net/ignore-differences-writers"
WHOLE_DATA_POINTERS = {"/data", "/stringData", "/binaryData"}
WHOLE_DATA_JQ = {".data", ".stringData", ".binaryData"}
STATEFULSET_POINTERS = {"/spec/selector", "/spec/serviceName", "/spec/volumeClaimTemplates",
                        "/spec/podManagementPolicy"}
STATEFULSET_JQ = (".spec.selector", ".spec.serviceName", ".spec.volumeClaimTemplates",
                  ".spec.podManagementPolicy")
DEVIATION = re.compile(
    r"\A(server-side-apply-missing|create-namespace-true|retry-missing|finalizer-reason-missing|"
    r"respect-ignore-differences-missing|"
    r"ignore-differences\[\d+\]:(unnamed|whole-data|statefulset-identity|writer-missing))\Z")
PLACEHOLDER = re.compile(r"\b(tbd|todo|fixme|xxx)\b", re.IGNORECASE)


class ConfigError(Exception):
    pass


def _map(value):
    return value if isinstance(value, dict) else {}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def spec_deviations(doc: dict) -> list[str]:
    """STD-IAC-08 deviations of one Application, in the order estate-platform reports them."""
    spec = _map(doc.get("spec"))
    metadata = _map(doc.get("metadata"))
    annotations = _map(metadata.get("annotations"))
    policy = _map(spec.get("syncPolicy"))
    options = [str(o).strip() for o in (policy.get("syncOptions") or [])]
    found = []
    if "ServerSideApply=true" not in options:
        found.append("server-side-apply-missing")
    if "CreateNamespace=true" in options and not _text(annotations.get(CREATE_NAMESPACE_REASON)):
        found.append("create-namespace-true")
    if not isinstance(policy.get("retry"), dict):
        found.append("retry-missing")
    finalizers = [str(f) for f in (metadata.get("finalizers") or [])]
    if any(f.startswith(FINALIZER) for f in finalizers) and not _text(annotations.get(FINALIZER_REASON)):
        found.append("finalizer-reason-missing")
    entries = spec.get("ignoreDifferences")
    if not isinstance(entries, list) or not entries:
        return found
    if "RespectIgnoreDifferences=true" not in options:
        found.append("respect-ignore-differences-missing")
    try:
        writers = json.loads(annotations.get(WRITERS) or "")
    except (TypeError, ValueError):
        writers = []
    if not isinstance(writers, list):
        writers = []
    for index, raw in enumerate(entries):
        entry = _map(raw)
        prefix = f"ignore-differences[{index}]"
        name = str(entry.get("name") or "").strip()
        if not name or name == "*":
            found.append(f"{prefix}:unnamed")
        pointers = [str(p).strip() for p in (entry.get("jsonPointers") or [])]
        expressions = [str(e).strip() for e in (entry.get("jqPathExpressions") or [])]
        if any(p in WHOLE_DATA_POINTERS for p in pointers) or any(e in WHOLE_DATA_JQ for e in expressions):
            found.append(f"{prefix}:whole-data")
        if str(entry.get("kind") or "") == "StatefulSet" and (
                any(p in STATEFULSET_POINTERS for p in pointers)
                or any(e.startswith(STATEFULSET_JQ) for e in expressions)):
            found.append(f"{prefix}:statefulset-identity")
        writer = writers[index] if index < len(writers) else None
        if not _text(writer):
            found.append(f"{prefix}:writer-missing")
    return found


def fixed_findings(doc: dict, project: str, repository: str) -> list[str]:
    """Rules no deviation row can waive, for one Application whose name is already matched."""
    found = []
    metadata = _map(doc.get("metadata"))
    spec = _map(doc.get("spec"))
    if metadata.get("namespace") != "argocd":
        found.append("metadata.namespace must be argocd")
    if spec.get("project") != project:
        found.append(f"spec.project must be {project}")
    if "source" in spec and "sources" in spec:
        found.append("spec must set source or sources, not both")
    sources = spec.get("sources") if "sources" in spec else [spec.get("source")]
    if not isinstance(sources, list) or not sources:
        found.append("spec.source is missing")
        sources = []
    wanted = {f"{base}{repository}{suffix}" for base in (MIRROR, GITHUB) for suffix in (".git", "")}
    for index, raw in enumerate(sources):
        source = _map(raw)
        label = f"source[{index}]"
        if source.get("repoURL") not in wanted:
            found.append(f"{label}.repoURL must be {repository} on the Git mirror or on github.com")
        if not REVISION.match(str(source.get("targetRevision") or "")):
            found.append(f"{label}.targetRevision must be main or a 40-character commit")
    destination = _map(spec.get("destination"))
    if destination.get("server") != IN_CLUSTER and destination.get("name") != "in-cluster":
        found.append(f"spec.destination must be the local cluster ({IN_CLUSTER})")
    policy = _map(spec.get("syncPolicy"))
    if _map(policy.get("automated")).get("prune") is True:
        found.append("syncPolicy.automated.prune must not be true")
    if any(str(o).replace(" ", "") == "Prune=true" for o in (policy.get("syncOptions") or [])):
        found.append("syncOptions must not set Prune=true")
    return found


def load_config(path: Path) -> list[dict]:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: cannot read ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or set(data) != {"schema_version", "roots"}:
        raise ConfigError(f"{path}: want a mapping with exactly schema_version: 1 and roots")
    roots = data["roots"]
    if not isinstance(roots, list) or not roots:
        raise ConfigError(f"{path}: roots must be a non-empty list (the check would pass with nothing to read)")
    seen_roots, seen_paths = set(), set()
    for index, root in enumerate(roots):
        where = f"{path}: roots[{index}]"
        if not isinstance(root, dict) or not {"root", "path", "repository", "applications"} <= set(root) \
                or not set(root) <= {"root", "path", "repository", "applications", "deviations"}:
            raise ConfigError(f"{where}: keys must be root, path, repository, applications and optional deviations")
        name, rel, repository = root["root"], root["path"], root["repository"]
        if not _text(name) or name in seen_roots:
            raise ConfigError(f"{where}: root must be a unique non-empty name")
        if not _text(rel) or rel.startswith("/") or ".." in Path(rel).parts or rel in seen_paths:
            raise ConfigError(f"{where}: path must be a unique relative directory inside the repository")
        if not isinstance(repository, str) or not REPOSITORY.match(repository):
            raise ConfigError(f"{where}: repository must be <owner>/<name>")
        seen_roots.add(name)
        seen_paths.add(rel)
        apps = root["applications"]
        if not isinstance(apps, list) or not apps:
            raise ConfigError(f"{where}: applications must be a non-empty list")
        keys = set()
        for row in apps:
            if not isinstance(row, dict) or set(row) != {"file", "name", "project"} \
                    or not all(_text(row[k]) for k in ("file", "name", "project")):
                raise ConfigError(f"{where}: each applications row needs exactly file, name and project")
            if "/" in row["file"] or not row["file"].endswith(MANIFEST_SUFFIXES):
                raise ConfigError(f"{where}: applications file {row['file']!r} must be a manifest directly under path")
            if row["name"] in keys:
                raise ConfigError(f"{where}: Application {row['name']!r} is listed twice")
            keys.add(row["name"])
        listed = {(row["file"], row["name"]) for row in apps}
        recorded = set()
        for row in root.get("deviations") or []:
            if not isinstance(row, dict) or set(row) != {"manifest", "application", "deviations", "reason"}:
                raise ConfigError(f"{where}: each deviations row needs exactly manifest, application, deviations and reason")
            key = (row["manifest"], row["application"])
            if key not in listed:
                raise ConfigError(f"{where}: deviations row {key} names no listed Application; remove the stale row")
            if key in recorded:
                raise ConfigError(f"{where}: deviations row {key} appears twice")
            recorded.add(key)
            ids = row["deviations"]
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) \
                    or not all(isinstance(i, str) and DEVIATION.match(i) for i in ids):
                raise ConfigError(f"{where}: deviations row {key} must list known STD-IAC-08 deviation ids once each")
            reason = _text(row["reason"])
            if len(reason) < 24 or PLACEHOLDER.search(reason):
                raise ConfigError(f"{where}: deviations row {key} needs a reason of at least 24 characters with no placeholder")
        if "deviations" in root and not isinstance(root["deviations"], list):
            raise ConfigError(f"{where}: deviations must be a list")
    return roots


def load_documents(path: Path):
    """The non-empty documents of a manifest, or None when it does not parse."""
    try:
        return [doc for doc in yaml.safe_load_all(path.read_text()) if doc is not None]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def check_root(repo_root: Path, root: dict) -> tuple[list[str], int]:
    findings = []
    rel = root["path"].rstrip("/")
    directory = repo_root / rel
    label = f"{root['root']} {rel}"
    if not directory.is_dir():
        return [f"{label}: directory is missing"], 0
    wanted: dict[str, dict[str, str]] = {}
    for row in root["applications"]:
        wanted.setdefault(row["file"], {})[row["name"]] = row["project"]
    recorded = {(row["manifest"], row["application"]): list(row["deviations"])
                for row in root.get("deviations") or []}
    present = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name.endswith(MANIFEST_SUFFIXES))
    for name in present:
        if name not in wanted:
            findings.append(f"{label}/{name}: manifest is not listed in applications (the root would apply an unchecked file)")
    count = 0
    for name, apps in sorted(wanted.items()):
        where = f"{label}/{name}"
        if name not in present:
            findings.append(f"{where}: listed file is missing")
            continue
        docs = load_documents(directory / name)
        if docs is None:
            findings.append(f"{where}: does not parse as YAML")
            continue
        seen = set()
        for index, doc in enumerate(docs):
            if not isinstance(doc, dict) or doc.get("apiVersion") != "argoproj.io/v1alpha1" \
                    or doc.get("kind") != "Application":
                findings.append(f"{where}: document {index} is not an argoproj.io/v1alpha1 Application")
                continue
            app = _map(doc.get("metadata")).get("name")
            if app not in apps:
                findings.append(f"{where}: document {index} declares an Application that is not listed for this file")
                continue
            if app in seen:
                findings.append(f"{where}: Application {app} is declared twice")
                continue
            seen.add(app)
            count += 1
            for finding in fixed_findings(doc, apps[app], root["repository"]):
                findings.append(f"{where}: Application {app}: {finding}")
            actual = spec_deviations(doc)
            listed = recorded.get((name, app), [])
            for deviation in actual:
                if deviation not in listed:
                    findings.append(f"{where}: Application {app}: breaks STD-IAC-08 ({deviation}); "
                                    "fix the spec or record the deviation with a reason")
            for deviation in listed:
                if deviation not in actual:
                    findings.append(f"{where}: Application {app}: recorded deviation {deviation} no longer occurs; "
                                    "remove it (the list only shrinks)")
        for app in sorted(set(apps) - seen):
            findings.append(f"{where}: listed Application {app} is not declared")
    return findings, count


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="path of the config file, relative to --repo-root")
    parser.add_argument("--repo-root", default=".", help="repository checkout (default: current directory)")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    config = repo_root / args.config
    try:
        roots = load_config(config)
    except ConfigError as exc:
        print(f"owner-root-children: {exc}", file=sys.stderr)
        return 2
    resolved = config.resolve()
    findings, total = [], 0
    for root in roots:
        if (repo_root / root["path"]).resolve() in resolved.parents:
            print(f"owner-root-children: {config} is inside {root['path']}, which the root applies; move it",
                  file=sys.stderr)
            return 2
        found, count = check_root(repo_root, root)
        findings += found
        total += count
    for finding in findings:
        print(f"FAIL {finding}")
    if findings:
        print(f"owner-root-children: {len(findings)} finding(s)")
        return 1
    print(f"owner-root-children: OK ({len(roots)} root(s), {total} Application(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
