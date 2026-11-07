#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/lint-images.py (rule R4, agents#5767 G6)."""
import datetime
import importlib.util
import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("li", root / "deploy/foundation/bin/lint-images.py")
li = importlib.util.module_from_spec(spec)
spec.loader.exec_module(li)
today = datetime.date(2026, 11, 7)
cases = {
    "bad": Counter({"R4-pull-always": 3, "R4-allow-expired": 1, "R4-allow-invalid": 1}),
    "good": Counter(),
}
failed = 0
for name, want in cases.items():
    findings, _ = li.lint_repo(root / "tests/lint-images/fixtures" / name, today=today)
    got = Counter(x.rule for x in findings if x.error)
    if got != want:
        failed += 1
        print(f"FAIL {name}: want {dict(want)} got {dict(got)}")
        for x in findings:
            print(f"  {x}")
if failed:
    sys.exit(f"lint-images self-test: {failed} failure(s)")
print(f"lint-images self-test: {len(cases)} case(s) OK")
