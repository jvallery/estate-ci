#!/usr/bin/env python3
"""The estate version detector's repository list (estate-ci#261, STD-OPS-02).

  renovate-repos.py check [FILE]        validate renovate/repositories.json
  renovate-repos.py active [FILE]       print the repositories in active batches
  renovate-repos.py all [FILE]          print every non-exception repository
  renovate-repos.py installed OWNER/REPO
      exit 0 if GitHub App vallery-pin-bot is installed on the repository,
      3 if it is not (HTTP 404), 1 on any other error. Needs APP_CLIENT_ID,
      APP_PRIVATE_KEY and $ESTATE_CI/app-token.py; mints no installation token
      and prints nothing secret.

stdlib only. FILE defaults to renovate/repositories.json next to this script.
"""
import importlib.util
import json
import os
import re
import sys
import urllib.error
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "renovate" / "repositories.json"
OWNERS = ("jvallery", "VerdifyConsultancy")
REPO_RE = re.compile(r"^(%s)/[A-Za-z0-9_.-]+$" % "|".join(OWNERS))


def load(path):
    return json.loads(Path(path).read_text())


def problems(doc):
    out = []
    active = doc.get("active_batches")
    if not isinstance(active, list) or not all(isinstance(b, int) and not isinstance(b, bool) for b in active):
        out.append("active_batches must be a list of integers")
        active = []
    seen = set()
    batches = set()
    for i, e in enumerate(doc.get("repositories") or []):
        repo = e.get("repo", "")
        if not REPO_RE.match(repo):
            out.append(f"entry {i}: repo {repo!r} is not OWNER/NAME with owner in {OWNERS}")
        if repo.lower() in seen:
            out.append(f"entry {i}: {repo} is listed twice")
        seen.add(repo.lower())
        b = e.get("batch")
        if b == "exception":
            if not str(e.get("note", "")).strip():
                out.append(f"{repo}: an exception entry needs a note giving the reason")
        elif isinstance(b, int) and not isinstance(b, bool) and b >= 0:
            batches.add(b)
        else:
            out.append(f"{repo}: batch must be a non-negative integer or \"exception\"")
        if not str(e.get("lane", "")).strip():
            out.append(f"{repo}: lane is required")
    if not seen:
        out.append("repositories is empty")
    for b in active:
        if b not in batches:
            out.append(f"active batch {b} has no repositories")
    # A listed repository is either scanned or an exception with a reason: a batch
    # that is never activated leaves its repositories silently unscanned.
    for b in sorted(batches - set(active)):
        repos = [e["repo"] for e in doc.get("repositories") or [] if e.get("batch") == b]
        out.append(f"batch {b} is not in active_batches, so {', '.join(repos)} would never be scanned")
    return out


def selected(doc, only_active):
    active = set(doc.get("active_batches") or [])
    for e in doc["repositories"]:
        b = e["batch"]
        if b == "exception":
            continue
        if only_active and b not in active:
            continue
        yield e["repo"]


def installed(repo):
    path = Path(os.environ["ESTATE_CI"]) / "app-token.py"
    spec = importlib.util.spec_from_file_location("app_token", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    owner, name = repo.split("/", 1)
    app = mod.jwt(os.environ["APP_CLIENT_ID"], os.environ["APP_PRIVATE_KEY"])
    try:
        inst = mod.call("GET", f"/repos/{owner}/{name}/installation", app)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 3
        print(f"installation lookup for {repo}: HTTP {e.code}", file=sys.stderr)
        return 1
    # Permission names and levels only (no ids or tokens): what a pass token can request.
    perms = ",".join(f"{k}={v}" for k, v in sorted((inst.get("permissions") or {}).items()))
    print(f"installation permissions for {repo}: {perms}", file=sys.stderr)
    return 0


def main(argv):
    if not argv or argv[0] not in ("check", "active", "all", "installed"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "installed":
        if len(argv) != 2 or not REPO_RE.match(argv[1]):
            print("usage: renovate-repos.py installed OWNER/REPO", file=sys.stderr)
            return 2
        return installed(argv[1])
    doc = load(argv[1] if len(argv) > 1 else DEFAULT)
    errs = problems(doc)
    if errs:
        for e in errs:
            print(f"renovate-repos: {e}", file=sys.stderr)
        return 1
    if cmd == "check":
        n = len(doc["repositories"])
        print(f"renovate-repos: OK ({n} repositories, active batches {doc['active_batches']})")
        return 0
    for repo in selected(doc, cmd == "active"):
        print(repo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
