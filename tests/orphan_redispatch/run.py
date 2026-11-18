#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/orphan-redispatch.py detect() and plan() (estate-ci#296)."""
import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("orr", root / "deploy/foundation/bin/orphan-redispatch.py")
orr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orr)
failed = 0


def check(name, got, want):
    global failed
    if got != want:
        failed += 1
        print(f"FAIL {name}: got {got!r} want {want!r}")


NOW = dt.datetime(2026, 11, 18, 10, 40, tzinfo=dt.timezone.utc)


def ts(minutes_ago):
    return (NOW - dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def job(jid, ago, status="queued", runner=None, label="ms-ci"):
    return {"id": jid, "name": f"j{jid}", "status": status, "created_at": ts(ago),
            "runner_name": runner, "labels": [label]}


def run(rid, jobs, attempt=1):
    return {"id": rid, "run_attempt": attempt, "status": "queued", "created_at": jobs[0]["created_at"], "jobs": jobs}


# 1. Orphaned: queued 40 min ago while a job created 10 min ago already has a runner.
runs = [run(1, [job(11, 40)]), run(2, [job(21, 10, "in_progress", "r-1")])]
check("orphan detected", sorted(orr.detect(runs, NOW, 15, 5)), [1])
# 2. Plain backlog: the oldest jobs run, newer ones wait. Nothing is orphaned.
runs = [run(1, [job(11, 40, "in_progress", "r-1")]), run(2, [job(21, 30)]), run(3, [job(31, 20)])]
check("backlog not orphaned", orr.detect(runs, NOW, 15, 5), {})
# 3. Too young (below --min-age) is left alone even when newer jobs run.
runs = [run(1, [job(11, 12)]), run(2, [job(21, 2, "in_progress", "r-1")])]
check("young job ignored", orr.detect(runs, NOW, 15, 5), {})
# 4. Within --grace of the newest started job is not orphaned.
runs = [run(1, [job(11, 30)]), run(2, [job(21, 27, "in_progress", "r-1")])]
check("inside grace ignored", orr.detect(runs, NOW, 15, 5), {})
# 5. Labels are independent: a running job on another set says nothing about ms-ci.
runs = [run(1, [job(11, 40)]), run(2, [job(21, 10, "in_progress", "r-1", label="ms-ci-gate")])]
check("other label ignored", orr.detect(runs, NOW, 15, 5), {})
# 6. --label-re limits detection.
runs = [run(1, [job(11, 40)]), run(2, [job(21, 10, "in_progress", "r-1")])]
check("label-re excludes", orr.detect(runs, NOW, 15, 5, re.compile("^other$")), {})
# 7. plan(): in-progress jobs defer, attempt cap skips, own run skips, oldest first, max-runs cap.
runs = [run(1, [job(11, 50)]),
        run(2, [job(21, 45), job(22, 44, "in_progress", "r-2")]),
        run(3, [job(31, 40)], attempt=3),
        run(4, [job(41, 35)]),
        run(5, [job(51, 30)]),
        run(6, [job(61, 5, "in_progress", "r-6")])]
orph = orr.detect(runs, NOW, 15, 5)
check("plan orphans", sorted(orph), [1, 2, 3, 4, 5])
got = orr.plan(runs, orph, own_run_id=5, max_attempt=3, max_runs=1)
check("plan decisions", [(r, a) for r, a, _ in got],
      [(1, "redispatch"), (2, "defer"), (3, "skip"), (4, "defer"), (5, "skip")])

# 8. Saturation guard (2026-11-18 estate-platform-ci): max 4, 4 in progress, an older lint job
# starved while newer gates jobs got runners. Without --max-runners it reads as an orphan; with
# it the label is saturated and nothing is flagged.
runs = [run(1, [job(11, 25, label="ep-ci")]),
        run(2, [job(21, 20, "in_progress", "r-1", label="ep-ci"), job(22, 20, label="ep-ci")]),
        run(3, [job(31, 10, "in_progress", "r-2", label="ep-ci")]),
        run(4, [job(41, 8, "in_progress", "r-3", label="ep-ci"), job(42, 8, "in_progress", "r-4", label="ep-ci")])]
check("saturated, no max: flagged", sorted(orr.detect(runs, NOW, 15, 5)), [1, 2])
check("saturated, max 4: not flagged", orr.detect(runs, NOW, 15, 5, max_runners={"ep-ci": 4}), {})
# 9. Idle capacity (the listener-restart orphan): 1 of 10 in progress, an old job still queued.
runs = [run(1, [job(11, 40)]), run(2, [job(21, 10, "in_progress", "r-1")])]
check("idle capacity, max 10: flagged", sorted(orr.detect(runs, NOW, 15, 5, max_runners={"ms-ci": 10})), [1])
# 10. Enforce with no --max-runners entry for the label: report only.
orph = orr.detect(runs, NOW, 15, 5)
check("unknown label reported", [(r, a) for r, a, _ in orr.plan(runs, orph, None, 3, 10, known_labels=set())],
      [(1, "report")])
check("known label redispatched", [(r, a) for r, a, _ in orr.plan(runs, orph, None, 3, 10, known_labels={"ms-ci"})],
      [(1, "redispatch")])
# 11. --max-runners parsing.
check("parse max-runners", orr.parse_max_runners("a=4, b=10"), {"a": 4, "b": 10})
try:
    orr.parse_max_runners("a=0")
    check("parse rejects 0", "accepted", "ValueError")
except ValueError:
    pass

if failed:
    print(f"orphan_redispatch: {failed} failure(s)")
    sys.exit(1)
print("orphan_redispatch: OK")
