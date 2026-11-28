#!/usr/bin/env python3
"""Self-test for scripts/renovate-repos.py check (estate-ci#261).

1. Every integer batch active, exceptions with a note: exit 0.
2. A non-exception entry in a batch missing from active_batches: exit 1, naming it.
3. An exception entry without a note: exit 1.
4. The real renovate/repositories.json passes.
5. count_opened charges only renovate/ pull requests created at or after the run start.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
script = root / "scripts" / "renovate-repos.py"


def check(doc):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(doc, f)
    try:
        r = subprocess.run([sys.executable, str(script), "check", f.name], capture_output=True, text=True)
    finally:
        Path(f.name).unlink()
    return r.returncode, r.stdout + r.stderr


def doc(active, entries):
    return {"active_batches": active, "repositories": [
        {"repo": r, "batch": b, "lane": "test", **({"note": n} if n else {})} for r, b, n in entries]}


cases = [
    ("all batches active", doc([0, 1], [("jvallery/a", 0, None), ("jvallery/b", 1, None),
                                         ("jvallery/c", "exception", "waiting on an install")]), 0, None),
    ("batch missing from active_batches", doc([0], [("jvallery/a", 0, None), ("jvallery/b", 2, None)]),
     1, "batch 2 is not in active_batches, so jvallery/b"),
    ("exception without a note", doc([0], [("jvallery/a", 0, None), ("jvallery/c", "exception", None)]),
     1, "needs a note"),
]
failed = 0
for name, d, want_rc, want_text in cases:
    rc, out = check(d)
    if rc != want_rc or (want_text and want_text not in out):
        failed += 1
        print(f"FAIL {name}: rc {rc} (want {want_rc}): {out.strip()}")
spec = importlib.util.spec_from_file_location("rr", script)
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)
prs = [
    {"head": {"ref": "renovate/github-actions"}, "created_at": "2026-11-28T09:20:00Z"},
    {"head": {"ref": "renovate/pyyaml-6.x"}, "created_at": "2026-11-28T09:17:00Z"},
    {"head": {"ref": "renovate/old"}, "created_at": "2026-11-27T09:20:00Z"},
    {"head": {"ref": "lane/12"}, "created_at": "2026-11-28T09:30:00Z"},
]
got = rr.count_opened(prs, "2026-11-28T09:17:00Z")
if got != 2:
    failed += 1
    print(f"FAIL count_opened: got {got}, want 2")
r = subprocess.run([sys.executable, str(script), "check"], capture_output=True, text=True)
if r.returncode != 0:
    failed += 1
    print(f"FAIL renovate/repositories.json: {r.stderr.strip()}")
if failed:
    sys.exit(1)
print(f"renovate_repos: OK ({len(cases)} fixtures, the PR budget count and renovate/repositories.json)")
