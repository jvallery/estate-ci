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
# (fixture, lint_repo kwargs) -> error rules wanted, warning rules wanted
cases = {
    ("bad", ()): (Counter({"R4-pull-always": 3, "R4-allow-expired": 1, "R4-allow-invalid": 1}), Counter()),
    ("good", ()): (Counter(), Counter()),
    # estate-ci#104: tag-only refs on any registry and Helm repository/tag pairs
    # without a digest; the allowlist covers them, an expired row does not.
    ("tags", (("tags", True),)): (Counter({"R4-tag-only": 4, "R4-allow-expired": 1}), Counter()),
    ("tags", (("tags", True), ("tags_error", False))): (Counter({"R4-allow-expired": 1}), Counter({"R4-tag-only": 4})),
    ("tags", ()): (Counter({"R4-allow-expired": 1}), Counter()),
    # A kustomization images: entry with a digest pins that name in every file it
    # reaches (resources, components, patches); newTag alone, a name with no entry
    # and a file no pinning kustomization reaches still fail. The allowlist
    # prefix * covers every image under its glob.
    ("kustomize", (("tags", True),)): (Counter({"R4-tag-only": 3}), Counter()),
}
failed = 0
for (name, kw), (want, want_warn) in cases.items():
    findings, _ = li.lint_repo(root / "tests/lint-images/fixtures" / name, today=today, **dict(kw))
    got = Counter(x.rule for x in findings if x.error)
    got_warn = Counter(x.rule for x in findings if not x.error)
    if got != want or got_warn != want_warn:
        failed += 1
        print(f"FAIL {name} {dict(kw)}: want {dict(want)} / warn {dict(want_warn)} got {dict(got)} / warn {dict(got_warn)}")
        for x in findings:
            print(f"  {x}")
# The reusable lint switches from warn to error on TAG_ONLY_ENFORCE_FROM.
spec = importlib.util.spec_from_file_location("lw", root / "deploy/foundation/bin/lint-workflows.py")
lw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lw)
start = datetime.date.fromisoformat(lw.TAG_ONLY_ENFORCE_FROM)
if lw.tag_mode(start - datetime.timedelta(days=1)) != "--tags=warn" or lw.tag_mode(start) != "--tags":
    failed += 1
    print("FAIL tag_mode: want --tags=warn before TAG_ONLY_ENFORCE_FROM and --tags from it")
if failed:
    sys.exit(f"lint-images self-test: {failed} failure(s)")
print(f"lint-images self-test: {len(cases) + 1} case(s) OK")
