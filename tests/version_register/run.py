#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/lint-version-register.py (STD-OPS-02, estate-ci#121)."""
import contextlib
import datetime
import importlib.util
import io
import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[2]
fixtures = root / "tests/version_register/fixtures"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vr = load("vr", root / "deploy/foundation/bin/lint-version-register.py")
today = datetime.date(2026, 11, 9)
schema = vr.load_schema()
# (fixture, --require) -> error rules wanted, warning rules wanted
cases = {
    ("good", True): (Counter(), Counter()),
    # The issue's seeded case: a row missing a required field (support) fails.
    ("missing-field", False): (Counter({"VR-schema": 1}), Counter()),
    ("patched", False): (Counter({"VR-schema": 1, "VR-patched-digest": 1}), Counter()),
    # ref rows: missing key, path outside the repo, missing file, copied values on a
    # pointer; plus a bad kind, an impossible date and a duplicate component.
    ("refs", False): (Counter({"VR-ref-missing": 3, "VR-schema": 2, "VR-date-invalid": 1, "VR-duplicate": 1}), Counter()),
    ("eol", False): (Counter(), Counter({"VR-eol-past": 1})),
    ("invalid-json", False): (Counter({"VR-invalid-json": 1}), Counter()),
    ("none", True): (Counter({"VR-missing": 1}), Counter()),
    ("none", False): (Counter(), Counter()),
}
failed = 0
for (name, require), (want, want_warn) in cases.items():
    findings, _ = vr.lint_path(fixtures / name, require, schema, today)
    got = Counter(x.rule for x in findings if x.error)
    got_warn = Counter(x.rule for x in findings if not x.error)
    if got != want or got_warn != want_warn:
        failed += 1
        print(f"FAIL {name} require={require}: want {dict(want)} / warn {dict(want_warn)} got {dict(got)} / warn {dict(got_warn)}")
        for x in findings:
            print(f"  {x}")


def rc(fn, *args, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kw)


# Exit codes: --strict fails on the past-EOL warning; the seeded bad file fails, the good one passes.
checks = [
    (rc(vr.main, [str(fixtures / "good")], today=today), 0),
    (rc(vr.main, [str(fixtures / "eol")], today=today), 0),
    (rc(vr.main, ["--strict", str(fixtures / "eol")], today=today), 1),
    (rc(vr.main, [str(fixtures / "missing-field" / "version-register.json")], today=today), 1),
]
# The reusable lint runs the check only for roots that hold a register (opt-in).
lw = load("lw", root / "deploy/foundation/bin/lint-workflows.py")
checks += [
    (rc(lw.run_version_register, [str(fixtures / "none")]), 0),
    (rc(lw.run_version_register, [str(fixtures / "good")]), 0),
    (rc(lw.run_version_register, [str(fixtures / "good"), str(fixtures / "missing-field")]), 1),
]
for i, (got, want) in enumerate(checks):
    if got != want:
        failed += 1
        print(f"FAIL exit-code check {i}: want {want} got {got}")
if failed:
    sys.exit(f"version-register self-test: {failed} failure(s)")
print(f"version-register self-test: {len(cases) + len(checks)} case(s) OK")
