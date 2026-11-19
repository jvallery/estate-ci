#!/usr/bin/env python3
"""Self-test for deploy/foundation/bin/orphan-redispatch.py (estate-ci#296)."""
from contextlib import redirect_stderr, redirect_stdout
import datetime as dt
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

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
      [(1, "would-redispatch"), (2, "defer"), (3, "skip"), (4, "defer"), (5, "skip")])

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
# 10. Retry eligibility requires exact first-attempt provider metadata.
orph = orr.detect(runs, NOW, 15, 5)
for invalid in (None, 0, 2, 3, "1", True):
    runs[0]["run_attempt"] = invalid
    check(f"attempt {invalid!r} rejected", orr.plan(runs, orph, None, 10, 10)[0][1], "skip")
runs[0]["run_attempt"] = 1
check("caller attempt cap still applies", orr.plan(runs, orph, None, 1, 10)[0][1], "skip")
del runs[0]["run_attempt"]
check("missing attempt rejected", orr.plan(runs, orph, None, 3, 10)[0][1], "skip")

# 11. --max-runners parsing.
check("parse max-runners", orr.parse_max_runners("a=4, b=10"), {"a": 4, "b": 10})
try:
    orr.parse_max_runners("a=0")
    check("parse rejects 0", "accepted", "ValueError")
except ValueError:
    pass

# 12. Exercise the CLI through the mocked GitHub REST boundary. A busy slot clears
# while a younger job has a runner. The report still surfaces the queued candidate,
# while even explicit enforce cannot POST a cancel or rerun.
candidate_run = run(1, [job(11, 40)])
running_run = run(2, [job(21, 10, "in_progress", "r-1"), job(22, 8, "in_progress", "r-2")])
running_run["status"] = "in_progress"


response_overrides = {}


def open_url(request, timeout):
    if request.get_method() != "GET":
        raise AssertionError(f"unexpected provider mutation: {request.get_method()} {request.full_url}")
    path = request.full_url[len(orr.API):]
    fixtures = {
        "/repos/jvallery/example/actions/runs?status=queued&per_page=100": {"workflow_runs": [candidate_run]},
        "/repos/jvallery/example/actions/runs?status=in_progress&per_page=100": {"workflow_runs": [running_run]},
        "/repos/jvallery/example/actions/runs/1/jobs?filter=latest&per_page=100": {"jobs": candidate_run["jobs"]},
        "/repos/jvallery/example/actions/runs/2/jobs?filter=latest&per_page=100": {"jobs": running_run["jobs"]},
    }
    rows = fixtures[path]
    data = {**rows, "total_count": len(next(iter(rows.values())))}
    response = response_overrides.get(path, data)
    return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())


def cli_capture(mode):
    output = io.StringIO()
    errors = io.StringIO()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture"}, clear=True), \
         patch.object(orr.urllib.request, "urlopen", side_effect=open_url), \
         patch.object(orr.dt, "datetime", wraps=dt.datetime) as clock, \
         redirect_stdout(output), redirect_stderr(errors):
        clock.now.return_value = NOW
        result = orr.main(["--repo", "jvallery/example", "--mode", mode, "--max-runners", "ms-ci=2"])
    return result, output.getvalue(), errors.getvalue()


def cli_report(mode):
    result, output, errors = cli_capture(mode)
    check(f"{mode} exit", result, 0)
    check(f"{mode} stderr", errors, "")
    return json.loads(output)


check("saturated enforce scan", cli_report("enforce")["orphaned_jobs"], 0)
running_run["jobs"][1]["status"] = "completed"
report = cli_report("enforce")
check("single idle snapshot reported", report["runs"],
      [{"run": 1, "action": "report", "why": "ARC listener assignment evidence unavailable", "jobs": ["j11"]}])
check("dry-run simulation retained", cli_report("dry-run")["runs"][0]["action"], "would-redispatch")
candidate_run["run_attempt"] = 2
check("repeat attempt skipped through provider", cli_report("enforce")["runs"][0]["action"], "skip")
del candidate_run["run_attempt"]
check("missing provider attempt isn't defaulted", cli_report("dry-run")["runs"][0]["action"], "skip")

# 13. An accepted but incomplete page must not turn into a successful zero.
for path, key in (
    ("/repos/jvallery/example/actions/runs?status=queued&per_page=100", "workflow_runs"),
    ("/repos/jvallery/example/actions/runs/1/jobs?filter=latest&per_page=100", "jobs"),
):
    for envelope, detail in (
        (b"{", "invalid JSON response"),
        ([], f"missing or invalid {key} list"),
        ({"total_count": 0}, f"missing or invalid {key} list"),
        ({key: None, "total_count": 0}, f"missing or invalid {key} list"),
        ({key: [None], "total_count": 1}, f"missing or invalid {key} list"),
        ({key: [], "total_count": True}, "invalid total_count"),
        ({key: [], "total_count": "0"}, "invalid total_count"),
        ({key: [], "total_count": -1}, "invalid total_count"),
        ({key: []}, "invalid total_count"),
        ({key: [], "total_count": 101}, "total_count=101 does not match 0 returned rows"),
        ({key: [{}] * 101, "total_count": 101}, "exceeds the supported 100-row page"),
    ):
        response_overrides[path] = envelope
        try:
            status, output, errors = cli_capture("dry-run")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            check(f"{key} {detail}: handled", type(exc).__name__, "scan failure")
        else:
            check(f"{key} {detail}: exit", status, 1)
            check(f"{key} {detail}: stdout", output, "")
            check(f"{key} {detail}: diagnostic", f"scan failed: {path}: {detail}" in errors, True)
    del response_overrides[path]

for status in ("queued", "in_progress"):
    response_overrides[f"/repos/jvallery/example/actions/runs?status={status}&per_page=100"] = {
        "workflow_runs": [], "total_count": 0
    }
zero = cli_report("dry-run")
check("valid empty queued and in-progress pages", (zero["runs_scanned"], zero["orphaned_jobs"], zero["runs"]),
      (0, 0, []))

if failed:
    print(f"orphan_redispatch: {failed} failure(s)")
    sys.exit(1)
print("orphan_redispatch: OK")
