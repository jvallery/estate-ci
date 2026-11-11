#!/usr/bin/env python3
"""Self-test for scripts/check-runner-secrets.py (estate-ci#115).

1. A live list holding every rendered Secret name passes.
2. Dropping one zot-push-<repo> name (a build set's mounted Secret) fails and names it.
"""
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("crs", root / "scripts/check-runner-secrets.py")
crs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crs)

refs = crs.rendered()
build = sorted(n for n in refs if n.startswith("zot-push-"))
assert build, "no zot-push-* Secret referenced by any rendered build set"
live = "\n".join(f"secret/{n}" for n in refs)
failed = 0
if crs.missing(refs, crs.live_names(live)):
    failed += 1
    print("FAIL complete live list reported missing Secrets")
drop = build[0]
gone = crs.missing(refs, crs.live_names(live.replace(f"secret/{drop}\n", "").replace(f"secret/{drop}", "")))
if list(gone) != [drop]:
    failed += 1
    print(f"FAIL dropping {drop}: want exactly it missing, got {list(gone)}")
if failed:
    sys.exit(1)
print(f"runner_secrets: OK ({len(refs)} names, missing {drop} detected)")
