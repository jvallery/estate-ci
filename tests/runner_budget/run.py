#!/usr/bin/env python3
"""Self-test for the per-worker CPU budget in scripts/render_runners.py (estate-ci#69).

1. runners.yaml at this revision renders and its budget passes.
2. Over-budget fixtures fail: the quota above the workers' runner budget, a
   bigger reserve, and bound: slots with the STD-CI-10 slot counts.
3. A slots-bound fixture whose ceilings fit passes.
"""
import copy
import importlib.util
import sys
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rr", root / "scripts/render_runners.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

cfg = yaml.safe_load((root / "runners.yaml").read_text())
rr.render(cfg)
max_pod = cfg["_max_pod_cpu"]
cap = cfg["capacity"]
quota = rr.quota_cpu(cap["quota"], rr.QUOTA_FILE.read_text())
failed = 0


def expect(name, cap, quota, ok):
    global failed
    try:
        lines = rr.budget(cap, max_pod, quota)
        got = True
    except rr.RenderError as exc:
        lines, got = str(exc).splitlines(), False
    if got != ok:
        failed += 1
        print(f"FAIL {name}: want {'pass' if ok else 'refused'}, got {'pass' if got else 'refused'}")
        for line in lines:
            print(f"  {line}")


expect("runners.yaml", cap, quota, True)
expect("quota over budget", cap, quota + 50, False)
bigger = copy.deepcopy(cap)
bigger["reserve_cpu"] = "20"
expect("reserve 20", bigger, quota, False)
slots = copy.deepcopy(cap)
slots["bound"] = "slots"
expect("bound slots at STD-CI-10 slots", slots, quota, False)
small = copy.deepcopy(slots)
for n in small["nodes"].values():
    n["slots"] = 1
    n["non_runner_cpu"] = "0"
expect("bound slots, 1 slot per worker", small, quota, True)
bad = copy.deepcopy(cap)
bad["bound"] = "nodes"
expect("unknown bound", bad, quota, False)
if failed:
    sys.exit(f"runner budget self-test: {failed} failure(s)")
print("runner budget self-test: 6 case(s) OK")
