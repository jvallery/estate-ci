#!/usr/bin/env python3
"""Report GitHub Actions jobs that may be orphaned by an ARC listener restart (estate-ci#296).

A runner-set change re-creates that set's ARC listener. The new listener session counts only
the jobs GitHub assigns to it from then on. Jobs that were already queued stay `queued` on
GitHub, but no runner is ever created for them, until someone cancels and re-runs the run.
This happened three times on 2026-11-18 (ep#966; monitoring-stack 09:59Z and 10:33Z).

GitHub-only signal: an old queued job without a runner is a candidate when a newer job
on the same runs-on label got a runner. Only open (queued and in-progress) runs are read.
Non-FIFO assignment can yield the same signal during ordinary starvation, including
when GitHub momentarily shows spare capacity. --max-runners LABEL=N[,LABEL=N] suppresses
candidates when a label already has at least N in-progress jobs.

Dry-run reports the candidates and simulates first-attempt redispatch. Enforce mode also
only reports because the caller supplies no ARC listener identity or job assignment
evidence. A run that already has a second or later attempt is ineligible even if the
prior retry was manual. Missing or invalid attempt metadata is also ineligible.

    orphan-redispatch.py [--repo OWNER/NAME] [--mode dry-run|enforce] [--min-age 15] [--grace 5]
                         [--max-runs 10] [--max-attempt 3] [--pace 5] [--label-re REGEX]
                         [--max-runners LABEL=N[,LABEL=N]]

Token: $GITHUB_TOKEN (the caller's, with actions: read). Repo: $GITHUB_REPOSITORY.
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


def parse_max_runners(s: str) -> dict:
    out = {}
    for part in filter(None, (x.strip() for x in (s or "").split(","))):
        lab, _, n = part.partition("=")
        if not lab or not n.isdigit() or int(n) < 1:
            raise ValueError("bad --max-runners entry %r (want LABEL=N)" % part)
        out[lab.strip()] = int(n)
    return out


def in_progress_by_label(runs: list) -> dict:
    out: dict = {}
    for run in runs:
        for j in run["jobs"]:
            if j.get("status") == "in_progress" and j.get("labels"):
                out[j["labels"][0]] = out.get(j["labels"][0], 0) + 1
    return out


def detect(runs: list, now: dt.datetime, min_age_min: float, grace_min: float, label_re=None,
           max_runners=None):
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
    busy = in_progress_by_label(runs)
    max_runners = max_runners or {}
    out: dict = {}
    for run in runs:
        for j in run["jobs"]:
            if j.get("status") != "queued" or j.get("runner_name") or not j.get("labels"):
                continue
            lab = j["labels"][0]
            if label_re is not None and not label_re.search(lab):
                continue
            if lab in max_runners and busy.get(lab, 0) >= max_runners[lab]:
                continue  # saturation makes the GitHub-only signal ambiguous
            created = parse_ts(j["created_at"])
            if (now - created).total_seconds() < min_age_min * 60:
                continue
            ns = newest_started.get(lab)
            if ns is None or (ns - created).total_seconds() < grace_min * 60:
                continue
            out.setdefault(run["id"], []).append(j)
    return out


def plan(runs: list, orphans: dict, own_run_id, max_attempt: int, max_runs: int, mode="dry-run"):
    """Order candidates oldest first and simulate eligibility or report them."""
    by_id = {r["id"]: r for r in runs}
    decisions = []
    for rid in sorted(orphans, key=lambda i: min(parse_ts(j["created_at"]) for j in orphans[i])):
        run = by_id[rid]
        attempt = run.get("run_attempt")
        if str(rid) == str(own_run_id):
            decisions.append((rid, "skip", "own run"))
        elif type(attempt) is not int or attempt != 1:
            decisions.append((rid, "skip", "requires provider run_attempt=1"))
        elif attempt >= max_attempt:
            decisions.append((rid, "skip", "attempt %s >= %s" % (attempt, max_attempt)))
        elif any(j.get("status") == "in_progress" for j in run["jobs"]):
            decisions.append((rid, "defer", "jobs in progress"))
        elif mode == "enforce":
            decisions.append((rid, "report", "ARC listener assignment evidence unavailable"))
        else:
            decisions.append((rid, "would-redispatch", "%d orphaned job(s)" % len(orphans[rid])))
    n = 0
    capped = []
    for rid, act, why in decisions:
        if act == "would-redispatch":
            n += 1
            if n > max_runs:
                act, why = "defer", "over --max-runs %d" % max_runs
        capped.append((rid, act, why))
    return capped


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo, self.token = repo, token

    def get(self, path: str):
        req = urllib.request.Request(API + path, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "estate-ci-orphan-redispatch"})
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
            d = self.get("/repos/%s/actions/runs?status=%s&per_page=100" % (self.repo, status))
            out += d.get("workflow_runs", [])
        # Only open runs: their in-progress and finished jobs show which labels are being
        # served. This keeps one cycle to about (open runs + 2) API calls.
        seen, runs = set(), []
        for r in out:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            jobs = self.get("/repos/%s/actions/runs/%s/jobs?filter=latest&per_page=100" % (self.repo, r["id"]))
            runs.append({"id": r["id"], "run_attempt": r.get("run_attempt"), "status": r["status"],
                         "created_at": r["created_at"], "name": r.get("name", ""),
                         "jobs": [{k: j.get(k) for k in ("id", "name", "status", "created_at", "runner_name", "labels")}
                                  for j in jobs.get("jobs", [])]})
        return runs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--mode", choices=("dry-run", "enforce"), default="dry-run",
                   help="report candidates; dry-run also simulates redispatch eligibility")
    p.add_argument("--min-age", type=float, default=15)
    p.add_argument("--grace", type=float, default=5)
    p.add_argument("--max-runs", type=int, default=10)
    p.add_argument("--max-attempt", type=int, default=3)
    p.add_argument("--pace", type=float, default=5, help="accepted for CLI compatibility")
    p.add_argument("--label-re", default="")
    p.add_argument("--max-runners", default="", help="LABEL=N[,LABEL=N]: each set's maxRunners")
    a = p.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not a.repo or not token:
        print("orphan-redispatch: need --repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN", file=sys.stderr)
        return 2
    try:
        max_runners = parse_max_runners(a.max_runners)
    except ValueError as e:
        print("orphan-redispatch: %s" % e, file=sys.stderr)
        return 2
    gh = GitHub(a.repo, token)
    now = dt.datetime.now(dt.timezone.utc)
    runs = gh.runs()
    orphans = detect(runs, now, a.min_age, a.grace, re.compile(a.label_re) if a.label_re else None,
                     max_runners)
    decisions = plan(runs, orphans, os.environ.get("GITHUB_RUN_ID"), a.max_attempt, a.max_runs, a.mode)
    results = [{"run": rid, "action": act, "why": why, "jobs": [j["name"] for j in orphans[rid]]}
               for rid, act, why in decisions]
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
