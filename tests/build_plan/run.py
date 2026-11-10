#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/build-plan.py (estate-ci#156).

Covers the behaviours reconciled from the five lane-repo ci/build_plan.py copies:
path-prefix matching, dispatched names and "all" (dispatch-only rows excluded),
the pushed range ("before"..HEAD, HEAD^..HEAD, every file without a parent),
--split flags and $REVISION, --test pre-build checks, $GITHUB_OUTPUT and
malformed images.tsv rows. Needs python3 and git (the estate-ci toolbox).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
planner = here.parents[1] / "deploy/foundation/bin/build-plan.py"
fixture = here / "fixture"
failed = 0
CLEAN = {k: v for k, v in os.environ.items()
         if k not in ("IMAGES", "CHANGED_PATHS", "BEFORE", "GITHUB_EVENT_PATH", "REVISION", "GITHUB_OUTPUT")}


def plan(root, *args, **env):
    e = dict(CLEAN, **env)
    p = subprocess.run([sys.executable, str(planner), "--root", str(root), *args],
                       capture_output=True, text=True, env=e, cwd=root)
    return p


def names(p):
    return [r["name"] for r in json.loads(p.stdout.strip().splitlines()[-1])]


def expect(label, cond, p=None):
    global failed
    if cond:
        print(f"ok   {label}")
    else:
        failed += 1
        print(f"FAIL {label}")
        if p is not None:
            print(f"     rc={p.returncode} stdout={p.stdout.strip()!r} stderr={p.stderr.strip()!r}")


def picks(label, root, want, *args, **env):
    p = plan(root, *args, **env)
    expect(f"{label} -> {want}", p.returncode == 0 and names(p) == want, p)
    return p


def refuses(label, root, *args, **env):
    p = plan(root, *args, **env)
    expect(f"{label} fails", p.returncode != 0, p)


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env=dict(CLEAN, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid"))


def head(root):
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "repo"
    shutil.copytree(fixture, root)

    # Changed paths ($CHANGED_PATHS replaces the git diff).
    picks("dir prefix", root, ["app"], CHANGED_PATHS="app/main.py")
    picks("file prefix", root, ["app"], CHANGED_PATHS="Dockerfile.app")
    picks("file prefix is not a name prefix", root, [], CHANGED_PATHS="Dockerfile.apple")
    picks("dir prefix matches the bare dir", root, ["web"], CHANGED_PATHS="web")
    picks("dir prefix is not a name prefix", root, [], CHANGED_PATHS="webx/a")
    picks("several, in images.tsv order", root, ["app", "web"], CHANGED_PATHS="web/a app/b")
    picks("no paths row never builds on a change", root, [], CHANGED_PATHS="solo/Dockerfile")

    # Dispatched names.
    picks("all skips dispatch-only rows", root, ["app", "web", "bad", "rag-svc"], "all")
    picks("named dispatch-only row", root, ["solo"], "solo")
    picks("$IMAGES", root, ["app", "web"], IMAGES="web app")
    picks("arguments win over $IMAGES", root, ["bad"], "bad", IMAGES="web")
    refuses("unknown name", root, "nope")

    # --split and $REVISION.
    out = Path(tmp) / "out"
    p = picks("split image leaves the matrix", root, ["app"], "--split", "rag-svc",
              CHANGED_PATHS="rag/x app/y", GITHUB_OUTPUT=str(out))
    expect("split flag printed", p.stdout.splitlines()[0] == "rag_svc=true", p)
    lines = out.read_text().splitlines()
    expect("GITHUB_OUTPUT images/split/flag",
           lines == ['images=[{"name": "app", "dockerfile": "Dockerfile.app", "context": "."}]',
                     'split={"rag-svc": true}', "rag_svc=true"], None)
    p = picks("split not picked", root, [], "--split", "rag-svc", CHANGED_PATHS="")
    expect("split flag false", p.stdout.splitlines()[0] == "rag_svc=false", p)
    refuses("unknown --split", root, "--split", "nope", "all")
    sha = "0123456789abcdef0123456789abcdef01234567"
    picks("revision with only split names", root, [], "--split", "rag-svc", "rag-svc", REVISION=sha)
    refuses("revision with a matrix name", root, "--split", "rag-svc", "rag-svc", "app", REVISION=sha)
    refuses("revision without names", root, "--split", "rag-svc", REVISION=sha, CHANGED_PATHS="rag/x")
    refuses("revision not 40-hex", root, "--split", "rag-svc", "rag-svc", REVISION="main")
    refuses("revision without --split", root, "app", REVISION=sha)

    # --test runs the picked images' checks only.
    picks("--test passing check", root, ["web"], "--test", "web")
    refuses("--test failing check", root, "--test", "bad")
    picks("failing check not run without --test", root, ["bad"], "bad")
    picks("failing check not run when not picked", root, ["app"], "--test", CHANGED_PATHS="app/a")

    # The pushed range, from git.
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    picks("no parent: every tracked file", root, [])  # fixture has only ci/images.tsv
    first = head(root)
    for d in ("app", "web"):
        (root / d).mkdir()
        (root / d / "f").write_text(d)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", d)
    picks("HEAD^..HEAD", root, ["web"])
    picks("$BEFORE..HEAD", root, ["app", "web"], BEFORE=first)
    event = Path(tmp) / "event.json"
    event.write_text(json.dumps({"before": first}))
    picks("event before..HEAD", root, ["app", "web"], GITHUB_EVENT_PATH=str(event))
    event.write_text(json.dumps({"before": "0" * 40}))
    picks("new-branch zero before falls back to HEAD^", root, ["web"], GITHUB_EVENT_PATH=str(event))
    event.write_text(json.dumps({"before": "f" * 40}))
    picks("unknown before falls back to HEAD^", root, ["web"], GITHUB_EVENT_PATH=str(event))
    p = subprocess.run([sys.executable, str(planner), "all"], capture_output=True, text=True,
                       env=CLEAN, cwd=root / "web")
    expect("root defaults to the git top level", p.returncode == 0 and len(names(p)) == 4, p)

    # Malformed images.tsv.
    bad = Path(tmp) / "bad"
    (bad / "ci").mkdir(parents=True)
    (bad / "ci/images.tsv").write_text("x\tDockerfile\t.\n")
    refuses("three columns", bad, "all")
    (bad / "ci/images.tsv").write_text("x\tDockerfile\t.\tx/\nx\tDockerfile\t.\tx/\n")
    refuses("duplicate name", bad, "all")
    (bad / "ci/alt.tsv").write_text("y\tDockerfile\t.\ty/\n")
    picks("--tsv", bad, ["y"], "--tsv", "ci/alt.tsv", "all")
    refuses("missing images.tsv", bad, "--tsv", "ci/none.tsv", "all")

print("build-plan self-test:", "FAILED" if failed else "OK")
sys.exit(1 if failed else 0)
