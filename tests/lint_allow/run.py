#!/usr/bin/env python3
"""Self-test for the recorded-exception allow file of lint-workflows.py (estate-ci#5).

Builds a throwaway repository with one excepted workflow (hosted label, tag pin) and
one unexcepted workflow, then checks: covered findings are excepted, the same rule in
another file still fails, expired, malformed and over-180-day rows fail, unused rows warn.
"""
import datetime
import importlib.util
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lw", root / "deploy/foundation/bin/lint-workflows.py")
lw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lw)

GUARDED = """name: upstream deploy
on: {workflow_dispatch: {}}
permissions: {contents: read}
jobs:
  deploy:
    if: github.repository == 'upstream/repo'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
OTHER = """name: ci
on: {pull_request: {}}
permissions: {contents: read}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
TODAY = datetime.date(2026, 11, 9)
ISSUE = "https://github.com/jvallery/estate-ci/issues/5"


def lint(allow: str):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / ".github/workflows").mkdir(parents=True)
        (repo / ".github/workflows/upstream-deploy.yml").write_text(GUARDED)
        (repo / ".github/workflows/ci.yml").write_text(OTHER)
        (repo / ".estate-ci").mkdir()
        (repo / lw.ALLOW_FILE).write_text(allow)
        found = []
        for f in lw.collect(repo):
            found += lw.lint_workflow(str(f), f.read_text(), False)
        kept, excepted = lw.apply_allow(repo, found, TODAY)
        rel = lambda xs: sorted((Path(x.path).name, x.rule, x.error) for x in xs)
        return rel(kept), rel(excepted)


failed = 0


def check(name, got, want):
    global failed
    if got != want:
        failed += 1
        print(f"FAIL {name}: want {want} got {got}")


rows = f"""# guarded upstream workflow (D-373 row)
.github/workflows/upstream-*.yml R1-hosted-label 2027-01-21 {ISSUE}
.github/workflows/upstream-*.yml R2-uses 2027-01-21 {ISSUE}
"""
kept, excepted = lint(rows)
check("covered", excepted, [("upstream-deploy.yml", "R1-hosted-label", True), ("upstream-deploy.yml", "R2-uses", True)])
check("uncovered file still fails", kept, [("ci.yml", "R1-hosted-label", True)])

kept, _ = lint(f".github/workflows/upstream-*.yml R1-hosted-label 2026-11-08 {ISSUE}\n")
check("expired row fails", [k for k in kept if k[1].startswith("allow")], [("workflow-lint-allow.txt", "allow-expired", True)])

kept, _ = lint(".github/workflows/upstream-*.yml R1-hosted-label soon\n")
check("malformed row fails", [k for k in kept if k[1].startswith("allow")], [("workflow-lint-allow.txt", "allow-invalid", True)])

kept, _ = lint(f".github/workflows/upstream-*.yml * 2027-01-21 {ISSUE}\n")
check("wildcard rule refused", [k for k in kept if k[1].startswith("allow")], [("workflow-lint-allow.txt", "allow-invalid", True)])

kept, _ = lint(f".github/workflows/upstream-*.yml R1-hosted-label 2027-06-01 {ISSUE}\n")
check("expiry over 180 days fails", [k for k in kept if k[1].startswith("allow")], [("workflow-lint-allow.txt", "allow-too-long", True)])

kept, _ = lint(rows + f".github/workflows/gone.yml R1-hosted-label 2027-01-21 {ISSUE}\n")
check("unused row warns", [k for k in kept if k[1].startswith("allow")], [("workflow-lint-allow.txt", "allow-unused", False)])

if failed:
    raise SystemExit(f"lint allow self-test: {failed} failure(s)")
print("lint allow self-test: OK")
