#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/owner-root-children.py (jvallery/estate-platform#1553).

Each case writes a small repository into a temporary directory, runs the check as a
command and compares the exit status and the findings it names. The good tree must
pass, and every rule must fire on the one change that breaks it.
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
script = root / "deploy/foundation/bin/owner-root-children.py"
MIRROR = "http://git-mirror.git-mirror.svc.cluster.local/jvallery/example.git"


def app(name="web", project="example", **spec):
    doc = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": name, "namespace": "argocd"},
        "spec": {
            "project": project,
            "source": {"repoURL": MIRROR, "targetRevision": "main", "path": "deploy/web"},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "web"},
            "syncPolicy": {"automated": {"prune": False, "selfHeal": True},
                           "syncOptions": ["CreateNamespace=false", "ServerSideApply=true"],
                           "retry": {"limit": 3}},
        },
    }
    doc["spec"].update(spec)
    return doc


def config(**root_changes):
    entry = {"root": "example-root", "path": "argocd", "repository": "jvallery/example",
             "applications": [{"file": "web.yaml", "name": "web", "project": "example"}]}
    entry.update(root_changes)
    return {"schema_version": 1, "roots": [entry]}


def run(files, cfg, config_path="ci/owner-root-children.yaml"):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        for rel, content in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else yaml.safe_dump_all(content))
        path = repo / config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cfg if isinstance(cfg, str) else yaml.safe_dump(cfg))
        proc = subprocess.run([sys.executable, str(script), "--repo-root", tmp, "--config", config_path],
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr


def edit(doc, fn):
    doc = copy.deepcopy(doc)
    fn(doc)
    return doc


good = app()
ignore = {"ignoreDifferences": [{"group": "apps", "kind": "Deployment", "name": "web",
                                 "jsonPointers": ["/spec/replicas"]}]}
with_ignore = edit(app(**ignore), lambda d: (
    d["metadata"].setdefault("annotations", {}).update({"estate.vallery.net/ignore-differences-writers": '["hpa"]'}),
    d["spec"]["syncPolicy"]["syncOptions"].append("RespectIgnoreDifferences=true")))
deviation = [{"manifest": "web.yaml", "application": "web", "deviations": ["retry-missing"],
              "reason": "The retry block arrives with the next spec change."}]
no_retry = edit(good, lambda d: d["spec"]["syncPolicy"].pop("retry"))

# name -> (files, config, expected exit status, text every run must print)
cases = {
    "good": ({"argocd/web.yaml": [good], "argocd/README.md": "not a manifest\n"}, config(), 0, "OK (1 root(s), 1 Application(s))"),
    "good-github-source": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["source"].update(
        repoURL="https://github.com/jvallery/example.git", targetRevision="0" * 40))]}, config(), 0, "OK"),
    "good-ignore-differences": ({"argocd/web.yaml": [with_ignore]}, config(), 0, "OK"),
    "good-recorded-deviation": ({"argocd/web.yaml": [no_retry]}, config(deviations=deviation), 0, "OK"),
    "prune-true": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["syncPolicy"]["automated"].update(prune=True))]},
                   config(), 1, "syncPolicy.automated.prune must not be true"),
    "prune-sync-option": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["syncPolicy"]["syncOptions"].append("Prune=true"))]},
                          config(), 1, "syncOptions must not set Prune=true"),
    "other-project": ({"argocd/web.yaml": [app(project="default")]}, config(), 1, "spec.project must be example"),
    "other-name": ({"argocd/web.yaml": [app(name="auth")]}, config(), 1, "declares an Application that is not listed"),
    "second-application": ({"argocd/web.yaml": [good, app(name="auth")]}, config(), 1, "document 1 declares an Application that is not listed"),
    "declared-twice": ({"argocd/web.yaml": [good, good]}, config(), 1, "Application web is declared twice"),
    "unlisted-file": ({"argocd/web.yaml": [good], "argocd/extra.yaml": [app(name="extra")]}, config(), 1,
                      "argocd/extra.yaml: manifest is not listed"),
    "missing-file": ({"argocd/README.md": "x\n"}, config(), 1, "listed file is missing"),
    "missing-directory": ({"other/web.yaml": [good]}, config(), 1, "directory is missing"),
    "application-set": ({"argocd/web.yaml": [edit(good, lambda d: d.update(kind="ApplicationSet"))]}, config(), 1,
                        "is not an argoproj.io/v1alpha1 Application"),
    "other-kind": ({"argocd/web.yaml": [good, {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}]},
                   config(), 1, "document 1 is not an argoproj.io/v1alpha1 Application"),
    "other-namespace": ({"argocd/web.yaml": [edit(good, lambda d: d["metadata"].update(namespace="frigate"))]}, config(), 1,
                        "metadata.namespace must be argocd"),
    "other-repository": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["source"].update(
        repoURL="http://git-mirror.git-mirror.svc.cluster.local/jvallery/other.git"))]}, config(), 1, "repoURL must be jvallery/example"),
    "branch-revision": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["source"].update(targetRevision="feature"))]},
                        config(), 1, "targetRevision must be main or a 40-character commit"),
    "second-source-elsewhere": ({"argocd/web.yaml": [edit(good, lambda d: (d["spec"].pop("source"), d["spec"].update(sources=[
        {"repoURL": MIRROR, "targetRevision": "main", "path": "a"},
        {"repoURL": "https://charts.example.invalid", "targetRevision": "1.0.0", "chart": "x"}])))]},
        config(), 1, "source[1].repoURL must be jvallery/example"),
    "other-cluster": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["destination"].update(server="https://10.0.0.1:6443"))]},
                      config(), 1, "spec.destination must be the local cluster"),
    "unparseable": ({"argocd/web.yaml": "kind: [unclosed\n"}, config(), 1, "does not parse as YAML"),
    "no-server-side-apply": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["syncPolicy"]["syncOptions"].remove("ServerSideApply=true"))]},
                             config(), 1, "breaks STD-IAC-08 (server-side-apply-missing)"),
    "create-namespace": ({"argocd/web.yaml": [edit(good, lambda d: d["spec"]["syncPolicy"]["syncOptions"].append("CreateNamespace=true"))]},
                         config(), 1, "breaks STD-IAC-08 (create-namespace-true)"),
    "no-retry": ({"argocd/web.yaml": [no_retry]}, config(), 1, "breaks STD-IAC-08 (retry-missing)"),
    "finalizer-without-reason": ({"argocd/web.yaml": [edit(good, lambda d: d["metadata"].update(
        finalizers=["resources-finalizer.argocd.argoproj.io"]))]}, config(), 1, "breaks STD-IAC-08 (finalizer-reason-missing)"),
    "ignore-without-writer": ({"argocd/web.yaml": [app(**ignore)]}, config(), 1, "ignore-differences[0]:writer-missing"),
    "ignore-unnamed-whole-data": ({"argocd/web.yaml": [edit(with_ignore, lambda d: d["spec"]["ignoreDifferences"][0].update(
        name="*", kind="Secret", jsonPointers=["/data"]))]}, config(), 1, "ignore-differences[0]:whole-data"),
    "ignore-statefulset-identity": ({"argocd/web.yaml": [edit(with_ignore, lambda d: d["spec"]["ignoreDifferences"][0].update(
        kind="StatefulSet", jsonPointers=["/spec/volumeClaimTemplates"]))]}, config(), 1, "ignore-differences[0]:statefulset-identity"),
    "stale-deviation": ({"argocd/web.yaml": [good]}, config(deviations=deviation), 1, "recorded deviation retry-missing no longer occurs"),
    "deviation-without-reason": ({"argocd/web.yaml": [no_retry]}, config(deviations=[dict(deviation[0], reason="todo")]), 2,
                                 "needs a reason"),
    "deviation-for-unknown-application": ({"argocd/web.yaml": [good]}, config(deviations=[dict(deviation[0], application="gone")]), 2,
                                          "names no listed Application"),
    "deviation-cannot-waive-prune": ({"argocd/web.yaml": [good]}, config(deviations=[dict(deviation[0], deviations=["prune"])]), 2,
                                     "must list known STD-IAC-08 deviation ids"),
    "empty-roots": ({"argocd/web.yaml": [good]}, {"schema_version": 1, "roots": []}, 2, "roots must be a non-empty list"),
    "config-inside-the-directory": ({"argocd/web.yaml": [good]}, config(), 2, "which the root applies"),
    "path-escapes": ({"argocd/web.yaml": [good]}, config(path="../argocd"), 2, "path must be a unique relative directory"),
    "unknown-config-key": ({"argocd/web.yaml": [good]}, config(allow_prune=True), 2, "keys must be root, path, repository"),
}
failed = 0
for name, (files, cfg, want, text) in cases.items():
    where = "argocd/owner-root-children.yml" if name == "config-inside-the-directory" else "ci/owner-root-children.yaml"
    status, output = run(files, cfg, where)
    if status != want or text not in output:
        failed += 1
        print(f"FAIL {name}: exit {status} (want {want}), output:\n{output}")
if failed:
    sys.exit(f"owner-root-children self-test: {failed} of {len(cases)} case(s) failed")
print(f"owner-root-children self-test: OK ({len(cases)} cases)")
