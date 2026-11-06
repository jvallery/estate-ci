#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/lint-workflows.py.

1. Each tests/lint/*.yml fixture declares the findings it must produce on its
   first line ("# expect: RULE ...", "~RULE" for a warning). The lint must
   report exactly that multiset.
2. If PyYAML is importable, the lint's stdlib YAML reader must agree with
   yaml.BaseLoader on every fixture and on this repo's own workflows.
"""
import importlib.util
import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lw", root / "deploy/foundation/bin/lint-workflows.py")
lw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lw)

failed = 0
fixtures = sorted((root / "tests/lint").glob("*.yml"))
for f in fixtures:
    text = f.read_text()
    first = text.splitlines()[0]
    assert first.startswith("# expect:"), f"{f}: first line must be '# expect: ...'"
    want = Counter(first.split(":", 1)[1].split())
    got = Counter(("" if x.error else "~") + x.rule for x in lw.lint_workflow(str(f), text, True))
    if got != want:
        failed += 1
        print(f"FAIL {f.name}: want {sorted(want.elements())} got {sorted(got.elements())}")
        for x in lw.lint_workflow(str(f), text, True):
            print(f"  {x}")

try:
    import yaml
except ImportError:
    yaml = None
    print("PyYAML not installed: parser parity check skipped")
if yaml:
    def norm(x):
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in x.items()}
        if isinstance(x, list):
            return [norm(v) for v in x]
        return "" if x is None else str(x)

    for f in fixtures + sorted((root / ".github/workflows").glob("*.y*ml")):
        text = f.read_text()
        if norm(lw.load(text)) != norm(yaml.load(text, Loader=yaml.BaseLoader)):
            failed += 1
            print(f"FAIL parser parity with PyYAML BaseLoader: {f}")

if failed:
    sys.exit(f"lint self-test: {failed} failure(s)")
print(f"lint self-test: {len(fixtures)} fixture(s) OK")
