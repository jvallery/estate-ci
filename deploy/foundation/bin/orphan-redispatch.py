#!/usr/bin/env python3
"""Detect and re-dispatch GitHub Actions jobs orphaned by an ARC listener restart (estate-ci#296).

A runner-set change re-creates that set's ARC listener. The new listener session counts only
the jobs GitHub assigns to it from then on. Jobs that were already queued stay `queued` on
GitHub, but no runner is ever created for them, until someone cancels and re-runs the run.
This happened three times on 2026-11-18 (ep#966; monitoring-stack 09:59Z and 10:33Z).

Signal, from GitHub alone. A scale set takes its jobs roughly in queue order. So a queued job
with no runner is orphaned when a job on the same runs-on label that was created more than
--grace minutes LATER already got a runner, and the job itself is older than --min-age minutes.
A plain backlog does not match, because there the oldest jobs start first. Only open (queued
and in-progress) runs are read.

Action (--mode enforce): for each run holding orphaned jobs, oldest first, up to --max-runs:
  * skip the run if any of its jobs is in progress (try again next cycle, so running work is
    never cancelled), if run_attempt >= --max-attempt (no re-run loops), or if it is this
    workflow's own run;
  * otherwise cancel the run, wait for it to complete, and POST rerun-failed-jobs. Completed jobs
    keep their results; only the cancelled and failed jobs run again.
--mode dry-run (the default) only reports. Writes are paced --pace seconds apart.

    orphan-redispatch.py [--repo OWNER/NAME] [--mode dry-run|enforce] [--min-age 15] [--grace 5]
                         [--max-runs 10] [--max-attempt 3] [--pace 5] [--label-re REGEX]

Token: $GITHUB_TOKEN (the caller's, with actions: write for enforce). Repo: $GITHUB_REPOSITORY.
It prints one JSON summary line and appends a table to $GITHUB_STEP_SUMMARY when that is set.
Standard library only.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def detect(runs: list, now: dt.datetime, min_age_min: float, grace_min: float, label_re=None):
    """Return {run_id: [orphaned job dicts]} for the given runs.

    runs: [{"id", "run_attempt", "status", "created_at", "jobs": [{"id", "name", "status",
    "created_at", "runner_name", "labels"}]}]. A job's label is its single runs-on label.
    """
    newest_started: dict = {}
    for run in runs:
        for j in run["jobs"]:
            if j.get("runner_name") and j.get("labels"):
                lab = j["labels"][0]
                t = parse_ts(j["created_at"])
                if lab not in newest_started or t > newest_started[lab]:
                    newest_started[lab] = t
    out: dict = {}
    for run in runs:
        for j in run["jobs"]:
            if j.get("status") != "queued" or j.get("runner_name") or not j.get("labels"):
                continue
            lab = j["labels"][0]
            if label_re is not None and not label_re.search(lab):
                continue
            created = parse_ts(j["created_at"])
            if (now - created).total_seconds() < min_age_min * 60:
                continue
            ns = newest_started.get(lab)
            if ns is None or (ns - created).total_seconds() < grace_min * 60:
                continue
            out.setdefault(run["id"], []).append(j)
    return out


def plan(runs: list, orphans: dict, own_run_id, max_attempt: int, max_runs: int):
    """Order runs oldest first and decide redispatch, defer or skip for each."""
    by_id = {r["id"]: r for r in runs}
    decisions = []
    for rid in sorted(orphans, key=lambda i: min(parse_ts(j["created_at"]) for j in orphans[i])):
        run = by_id[rid]
        if str(rid) == str(own_run_id):
            decisions.append((rid, "skip", "own run"))
        elif int(run.get("run_attempt", 1)) >= max_attempt:
            decisions.append((rid, "skip", "attempt %s >= %s" % (run.get("run_attempt"), max_attempt)))
        elif any(j.get("status") == "in_progress" for j in run["jobs"]):
            decisions.append((rid, "defer", "jobs in progress"))
        else:
            decisions.append((rid, "redispatch", "%d orphaned job(s)" % len(orphans[rid])))
    n = 0
    capped = []
    for rid, act, why in decisions:
        if act == "redispatch":
            n += 1
            if n > max_runs:
                act, why = "defer", "over --max-runs %d" % max_runs
        capped.append((rid, act, why))
    return capped


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo, self.token = repo, token

    def call(self, method: str, path: str):
        req = urllib.request.Request(API + path, method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "estate-ci-orphan-redispatch"})
        if method != "GET":
            req.data = b""
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and attempt == 1:
                    time.sleep(60)
                    continue
                raise

    def runs(self):
        out = []
        for status in ("queued", "in_progress"):
            d = self.call("GET", "/repos/%s/actions/runs?status=%s&per_page=100" % (self.repo, status))
            out += d.get("workflow_runs", [])
        # Only open runs: their in-progress and finished jobs show which labels are being
        # served. This keeps one cycle to about (open runs + 2) API calls.
        seen, runs = set(), []
        for r in out:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            jobs = self.call("GET", "/repos/%s/actions/runs/%s/jobs?filter=latest&per_page=100" % (self.repo, r["id"]))
            runs.append({"id": r["id"], "run_attempt": r.get("run_attempt", 1), "status": r["status"],
                         "created_at": r["created_at"], "name": r.get("name", ""),
                         "jobs": [{k: j.get(k) for k in ("id", "name", "status", "created_at", "runner_name", "labels")}
                                  for j in jobs.get("jobs", [])]})
        return runs

    def redispatch(self, rid) -> str:
        self.call("POST", "/repos/%s/actions/runs/%s/cancel" % (self.repo, rid))
        for _ in range(24):
            time.sleep(5)
            if self.call("GET", "/repos/%s/actions/runs/%s" % (self.repo, rid)).get("status") == "completed":
                break
        else:
            return "cancel-timeout"
        self.call("POST", "/repos/%s/actions/runs/%s/rerun-failed-jobs" % (self.repo, rid))
        return "redispatched"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--mode", choices=("dry-run", "enforce"), default="dry-run")
    p.add_argument("--min-age", type=float, default=15)
    p.add_argument("--grace", type=float, default=5)
    p.add_argument("--max-runs", type=int, default=10)
    p.add_argument("--max-attempt", type=int, default=3)
    p.add_argument("--pace", type=float, default=5)
    p.add_argument("--label-re", default="")
    a = p.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not a.repo or not token:
        print("orphan-redispatch: need --repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN", file=sys.stderr)
        return 2
    gh = GitHub(a.repo, token)
    now = dt.datetime.now(dt.timezone.utc)
    runs = gh.runs()
    orphans = detect(runs, now, a.min_age, a.grace, re.compile(a.label_re) if a.label_re else None)
    decisions = plan(runs, orphans, os.environ.get("GITHUB_RUN_ID"), a.max_attempt, a.max_runs)
    results = []
    for rid, act, why in decisions:
        res = act
        if act == "redispatch":
            if a.mode == "enforce":
                try:
                    res = gh.redispatch(rid)
                except urllib.error.HTTPError as e:
                    res = "error %s" % e.code
                time.sleep(a.pace)
            else:
                res = "would-redispatch"
        results.append({"run": rid, "action": res, "why": why,
                        "jobs": [j["name"] for j in orphans[rid]]})
    summary = {"repo": a.repo, "mode": a.mode, "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "runs_scanned": len(runs), "orphaned_jobs": sum(len(v) for v in orphans.values()),
               "runs": results}
    print(json.dumps(summary, sort_keys=True))
    sf = os.environ.get("GITHUB_STEP_SUMMARY")
    if sf:
        with open(sf, "a") as f:
            f.write("### Orphaned jobs (%s)\n\n%d orphaned job(s) in %d run(s) of %d scanned.\n\n"
                    % (a.mode, summary["orphaned_jobs"], len(results), len(runs)))
            if results:
                f.write("| run | action | why | jobs |\n|---|---|---|---|\n")
                for r in results:
                    f.write("| %s | %s | %s | %s |\n" % (r["run"], r["action"], r["why"], ", ".join(r["jobs"])))
    return 1 if any(str(r["action"]).startswith("error") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
