#!/usr/bin/env python3
"""Pick the images to build from a repo's ci/images.tsv (estate-ci#156; stdlib only).

The one estate image build planner: lane repos call it through
.github/workflows/build-plan.yml (or as $ESTATE_CI/build-plan.py) instead of
carrying their own ci/build_plan.py. Run it from the caller repo's checkout.

    build-plan.py [options]               images whose paths changed in the pushed range
                                          (event "before"..HEAD; HEAD^..HEAD when unknown;
                                          every file when there is no parent)
    build-plan.py [options] all           every image that has trigger paths
    build-plan.py [options] NAME ...      the named images (unknown names fail)
    With no names, $IMAGES (if set) is read as the name list (a dispatch input).

Options:
    --root DIR     caller repository root (default: git top level of the cwd)
    --tsv PATH     images file, relative to --root (default ci/images.tsv)
    --test         also run each picked image's pre-build check (column 5) and fail
                   if any check fails
    --split NAME   keep image NAME out of the matrix and report it as its own flag
                   (for an image a caller builds in a dedicated job); repeatable

Environment: $CHANGED_PATHS (whitespace-separated paths) replaces the git diff;
$BEFORE overrides the push event's "before" SHA (read from $GITHUB_EVENT_PATH,
which GitHub and Forgejo both set); $REVISION, a 40-hex commit for a split
image's dedicated job, is accepted only when every requested name is a --split
name.

images.tsv: tab-separated name, dockerfile, context, paths, optional check; "#"
starts a comment line. Paths are space-separated path prefixes (a trailing "/"
means a directory). A row with no paths is dispatch-only: only its name builds
it, not "all" and not a change.

Output: one "<flag>=true|false" line per --split name (flag is the name with "-"
replaced by "_"), then, as the last line, a JSON array of {name, dockerfile,
context}. Under a forge it also writes images=<json>, split=<json object of
name: bool> and each <flag>=<bool> to $GITHUB_OUTPUT.
"""
import argparse
import json
import os
import re
import subprocess
import sys


def fail(msg):
    sys.exit(f"build-plan: {msg}")


def git(root, *args, check=True):
    p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        fail(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def load_rows(path):
    rows, seen = [], set()
    try:
        fh = open(path)
    except OSError as e:
        fail(f"cannot read {path}: {e.strerror}")
    with fh:
        for n, line in enumerate(fh, 1):
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) not in (4, 5) or not all(c.strip() for c in cols[:3]):
                fail(f"{path}:{n}: want name, dockerfile, context, paths[, check] (tab-separated)")
            name = cols[0].strip()
            if name in seen:
                fail(f"{path}:{n}: duplicate image {name}")
            seen.add(name)
            rows.append({"name": name, "dockerfile": cols[1].strip(), "context": cols[2].strip(),
                         "paths": cols[3].split(), "check": cols[4].strip() if len(cols) == 5 else ""})
    return rows


def matches(changed, prefix):
    if prefix.endswith("/"):
        return changed.startswith(prefix) or changed == prefix.rstrip("/")
    return changed == prefix or changed.startswith(prefix + "/")


def changed_paths(root):
    if "CHANGED_PATHS" in os.environ:
        return os.environ["CHANGED_PATHS"].split()

    def known(rev):
        return bool(rev) and git(root, "cat-file", "-e", rev + "^{commit}", check=False).returncode == 0

    # A push can carry several commits (rebase merges): diff the whole pushed range.
    before = os.environ.get("BEFORE", "").strip()
    if not before and os.environ.get("GITHUB_EVENT_PATH"):
        try:
            with open(os.environ["GITHUB_EVENT_PATH"]) as fh:
                before = str(json.load(fh).get("before") or "")
        except (OSError, ValueError, AttributeError):
            before = ""
    base = before if before.strip("0") and known(before) else ("HEAD^" if known("HEAD^") else "")
    args = ["diff", "--name-only", base, "HEAD"] if base else ["ls-files"]
    return git(root, *args).stdout.split()


def main(argv):
    ap = argparse.ArgumentParser(prog="build-plan.py", add_help=True)
    ap.add_argument("--root")
    ap.add_argument("--tsv", default="ci/images.tsv")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--split", action="append", default=[])
    ap.add_argument("names", nargs="*")
    a = ap.parse_args(argv)

    root = a.root or git(os.getcwd(), "rev-parse", "--show-toplevel").stdout.strip()
    rows = load_rows(os.path.join(root, a.tsv))
    names = {r["name"] for r in rows}
    unknown_split = [s for s in a.split if s not in names]
    if unknown_split:
        fail(f"--split names unknown image(s): {' '.join(unknown_split)}")

    want = (" ".join(a.names) or os.environ.get("IMAGES", "")).split()
    if want:
        unknown = [w for w in want if w != "all" and w not in names]
        if unknown:
            fail(f"unknown image(s): {' '.join(unknown)}")
        pick = [r for r in rows if r["name"] in want or ("all" in want and r["paths"])]
    else:
        changed = changed_paths(root)
        pick = [r for r in rows if any(matches(c, p) for c in changed for p in r["paths"])]

    revision = os.environ.get("REVISION", "").strip()
    if revision and (not want or not a.split or any(w not in a.split for w in want)
                     or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        fail("revision is accepted only as a 40-hex commit with only --split images requested")

    split = {s: any(r["name"] == s for r in pick) for s in a.split}
    matrix = json.dumps([{k: r[k] for k in ("name", "dockerfile", "context")}
                         for r in pick if r["name"] not in split])
    flags = [(s.replace("-", "_"), "true" if v else "false") for s, v in split.items()]
    for k, v in flags:
        print(f"{k}={v}")
    print(matrix)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"images={matrix}\nsplit={json.dumps(split)}\n")
            fh.writelines(f"{k}={v}\n" for k, v in flags)

    failed = []
    for r in pick if a.test else []:
        if r["check"]:
            print(f"check {r['name']}: {r['check']}", file=sys.stderr, flush=True)
            if subprocess.run(["bash", "-c", r["check"]], cwd=root).returncode != 0:
                failed.append(r["name"])
    if failed:
        fail(f"pre-build check failed: {' '.join(failed)}")


if __name__ == "__main__":
    main(sys.argv[1:])
