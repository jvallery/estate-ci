#!/usr/bin/env python3
"""Fixtures for deploy/foundation/bin/retention-projection.py (estate-ci#263)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "deploy/foundation/bin/retention-projection.py")
CASES = [
    # (name, diff, body, expected exit code)
    ("retention change, no heading", "retention.diff", "body-none.md", 1),
    ("retention change, empty heading", "retention.diff", "body-empty-heading.md", 1),
    ("retention change, projection heading", "retention.diff", "body-heading.md", 0),
    ("reduction, one-line form", "reduction.diff", "body-reduction.md", 0),
    ("reduction, no projection", "reduction.diff", "body-none.md", 1),
    ("non-retention diff (ttl, comment, prose)", "other.diff", "body-none.md", 0),
]
failed = 0
for name, diff, body, want in CASES:
    res = subprocess.run([sys.executable, SCRIPT, "--event", "", "--diff-file", os.path.join(HERE, "fixtures", diff),
                          "--body-file", os.path.join(HERE, "fixtures", body)], capture_output=True, text=True)
    ok = res.returncode == want
    failed += not ok
    print("%s %s (exit %d, want %d)" % ("ok  " if ok else "FAIL", name, res.returncode, want))
    if not ok:
        print(res.stdout + res.stderr)
if failed:
    sys.exit("retention_projection: %d case(s) failed" % failed)
print("retention_projection: OK")
