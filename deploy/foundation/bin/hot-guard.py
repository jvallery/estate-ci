#!/usr/bin/env python3
"""estate-ci node8 hot guard (D-67). Runners always lose to Frigate.

Runs every minute as CronJob ci-runner-hot-guard in ci-runners. stdlib only.

Each run reads trip signals from Prometheus (the queries are in the config
ConfigMap). Signals have a tier (COORD ruling 2026-11-07T03:49Z: runners always
lose to Frigate, but node CPU alone must not kill in-flight jobs):

  HARD trip      -> (Frigate degraded or restarted, node memory) write a reason
                    under the node name in ConfigMap ci-runner-slot-hold, so the
                    slot advertiser (CronJob ci-runner-slots) advertises 0
                    agentfleet.vallery.net/ci-runner-slot on the node within
                    about a minute (no new runners land). If the previous run
                    had already recorded a HARD trip (confirmed), also evict up
                    to evict_per_run of the node's runner Pods, newest first;
                    each later run evicts more while the trip holds.
  signal missing -> hold the node at 0 (fail closed) but evict nothing.
  SOFT trip      -> (node CPU, Frigate clean) write "cap=<soft_cap> ..." so the
                    advertiser sets min(desired, soft_cap). Nothing is
                    evicted; runners drain as their jobs finish.
  hard + clean   -> once no trip has been seen for clean_seconds, drop the hold
                    (or step down to the soft cap if the soft signal still trips).
  soft + calm    -> drop the cap once soft_recover holds (CPU max over 10 min
                    below 0.75). The advertiser then restores the desired slots.

Per-node reserve (estate-ci#69, D-486: runners may fill a worker to 95% of
allocatable). The quota bounds runner requests only in aggregate, so for each
node in config `reserve.nodes` (node4-7) the guard also reads that node's CPU
busy ratio. Above `reserve.trip` it writes "cap=<reserve.cap> ... reserve:..."
(`reserve.node_caps` overrides the cap per node; a held node is re-capped when it changes)
under the node, so the slot advertiser offers min(desired, cap) there and no
new runner lands until the node calms; nothing is evicted, and in-flight jobs
finish. The cap drops once `reserve.recover` holds (max over 30 min below
0.75). An empty reserve result, or `reserve.coverage` below its threshold (the
node's own CPU samples are missing because it is saturated), counts as hot.
A failed query (Prometheus unreachable) changes nothing, so CI on node4-7 does
not stop because Prometheus is down; a Prometheus error still makes the run
exit 1. The guard never
touches a hold value it did not write as a reserve cap.

Memory headroom (estate-ci#248). With config `reserve.memory`, the guard also reads each
reserve node's free memory requests from Prometheus (allocatable minus the requests of
Pending/Running Pods, kube-state-metrics; no pod RBAC) and computes
cap = runners on the node + floor((free - reserve_gib) / per_runner_gib), bounded by the
node's slots. Below the slots it writes "cap=<cap> ... reserve:mem<..." (the same reserve
tag, so the slot advertiser and foreign-hold rules are unchanged); a lower CPU or memory cap
wins. A memory hold lifts only once free >= reserve_gib + recover_gib. A failed or empty
memory query changes nothing (fail safe). `reserve.memory.mode: dry-run` logs the decision
under "memory" and writes nothing for it.

The guard has no node permission. It writes only its own ConfigMap and
evicts only Pods that carry the estate-ci.vallery.net/profile label in
ci-runners. GUARD_MODE=dry-run does every API write with dryRun=All.
GUARD_FORCE_TRIP=<reason> injects a confirmed HARD trip (evicts on the first run), for the forced-trip test.
Every run prints one JSON line. The run exits 1 on any API or Prometheus error,
after the fail-closed hold is attempted.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

NAMESPACE = "ci-runners"
HOLD_CM = "ci-runner-slot-hold"
PROFILE_LABEL = "estate-ci.vallery.net/profile"
LAST_TRIP = "estate-ci.vallery.net/last-trip"
SINCE = "estate-ci.vallery.net/held-since"
CONFIG_PATH = os.environ.get("GUARD_CONFIG", "/etc/hot-guard/config.json")
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
TIMEOUT = 10
# Named field manager for every ConfigMap write (R21 / STD-IAC-01): without it the API
# server records urllib's default "Python-urllib", which the estate-audit field-manager
# inventory cannot attribute. Registered in estate-platform r21-writers.json
# field_manager_allow.
FIELD_MANAGER = "estate-ci-hot-guard"
READY_RUN = 4
PROM_ATTEMPTS = 3
# estate-ci#280: one transient kube API miss (a timeout or refused connection, not an
# HTTP status) is retried once after KUBE_RETRY_SLEEP seconds before it counts as an
# error and fails the run. Only idempotent methods retry: GET, and the ConfigMap
# PATCH/PUT that rewrites the same hold. A POST (eviction, ConfigMap create) is sent once,
# because a retry after a lost reply would turn a success into a 404/409.
KUBE_ATTEMPTS = 2
KUBE_RETRY_SLEEP = 1


class GuardError(Exception):
    pass


# ---------------------------------------------------------------- pure logic

def evaluate(signals: dict, values: dict) -> tuple[list[str], list[str]]:
    """Return (tripped, missing) signal names. values[name] is a float or None."""
    tripped, missing = [], []
    for name, spec in signals.items():
        value = values.get(name)
        if value is None:
            if spec.get("required", True):
                missing.append(name)
            continue
        if spec["op"] == ">" and value > spec["threshold"]:
            tripped.append(name)
        elif spec["op"] == "<" and value < spec["threshold"]:
            tripped.append(name)
    return tripped, missing


def hold_cap(reason: str | None) -> int | None:
    """Slot cap of a hold value: None if empty, N for a leading "cap=N", else 0 (closed)."""
    text = (reason or "").strip()
    if not text:
        return None
    head = text.split()[0]
    if head.startswith("cap=") and head[4:].isdigit():
        return int(head[4:])
    return 0


def decide(now: dt.datetime, tripped: list[str], missing: list[str], hold: dict | None,
           clean_seconds: int, soft: list[str] | None = None, recovered: bool = False) -> dict:
    """Pure decision. tripped = HARD trips, soft = SOFT trips. hold is None (not held) or
    {"reason", "since", "last_trip", "cap"}; cap 0 is a hard hold, N > 0 a soft cap.
    recovered: the soft_recover condition holds (CPU calm long enough to drop a soft cap).

    Returns {"hold": "set"|"soft"|"keep"|"clear"|"none", "evict": bool, "reason": str}.
    """
    soft = soft or []
    if tripped:
        # Close slots on the first HARD run; evict only when the previous run had
        # already confirmed a HARD trip (COORD 2026-11-07T04:08Z: delete runners only
        # on a confirmed Frigate signal).
        confirmed = bool(hold) and not hold.get("cap") and " trip:" in (hold.get("reason") or "")
        return {"hold": "set", "evict": confirmed, "reason": "trip:" + ",".join(sorted(tripped))}
    if missing:
        return {"hold": "set", "evict": False, "reason": "missing:" + ",".join(sorted(missing))}
    soft_reason = "soft:" + ",".join(sorted(soft))
    if hold is not None and not hold.get("cap"):
        # Hard (or blind) hold: cool down for clean_seconds before reopening.
        last = hold.get("last_trip") or hold.get("since") or now
        if (now - last).total_seconds() < clean_seconds:
            return {"hold": "keep", "evict": False, "reason": "cooling"}
        if soft:
            return {"hold": "soft", "evict": False, "reason": soft_reason}
        return {"hold": "clear", "evict": False, "reason": "clean-for-%ds" % clean_seconds}
    if soft:
        if hold is not None:
            return {"hold": "keep", "evict": False, "reason": "soft-capped"}
        return {"hold": "soft", "evict": False, "reason": soft_reason}
    if hold is None:
        return {"hold": "none", "evict": False, "reason": "clean"}
    if recovered:
        return {"hold": "clear", "evict": False, "reason": "soft-recovered"}
    return {"hold": "keep", "evict": False, "reason": "soft-capped"}


RESERVE_TAG = " reserve:"


def reserve_decide(busy: float | None, calm: float | None, reason: str | None, trip: dict, recover: dict,
                   coverage: float | None = 1.0, cover: dict | None = None, failed: bool = False) -> str:
    """Per-node reserve decision: "set", "clear", "keep" (no write) or "foreign" (a hold this
    tier did not write; leave it alone). busy/calm are the trip/recover signal values and
    coverage the share of the node's expected CPU samples present over the recover window.

    A query that raised (failed=True, Prometheus unreachable) writes nothing. An empty
    trip/recover/coverage result or coverage below cover's threshold means the node's own
    telemetry is stalling, which happens when the node is saturated (estate-ci#177 follow-up), so it
    counts as hot: an unheld node is capped and a held node stays capped."""
    text = (reason or "").strip()
    if text and RESERVE_TAG not in " " + text:
        return "foreign"
    held = bool(text)
    if failed:
        return "keep"
    partial = coverage is None or (cover is not None and evaluate({"c": cover}, {"c": coverage})[0])
    if busy is None or calm is None or partial or evaluate({"t": trip}, {"t": busy})[0]:
        return "keep" if held else "set"
    if held and evaluate({"r": recover}, {"r": calm})[0]:
        return "clear"
    return "keep"


def reserve_cap(res: dict, node: str) -> int:
    """The node's reserve cap: reserve.node_caps[node] when set, else reserve.cap (estate-ci, pi#868 CI slots)."""
    return int((res.get("node_caps") or {}).get(node, res["cap"]))


def reserve_recap(reason: str | None, cap: int) -> str | None:
    """A held reserve value rewritten to cap, keeping its timestamp and tag; None when it already
    carries cap or is not a reserve hold. Lets a config cap change reach a node that is already held."""
    text = (reason or "").strip()
    if RESERVE_TAG not in " " + text or hold_cap(text) in (None, cap):
        return None
    return re.sub(r"^cap=\d+", "cap=%d" % cap, text)


MEM_TAG = "reserve:mem<"


def memory_cap(free_gib: float, runners: int, slots: int, reserve_gib: float, per_runner_gib: float) -> int:
    """Slots a worker may offer given its free memory requests: the runners it already has plus
    as many more as fit above reserve_gib, bounded by slots (estate-ci#248)."""
    extra = int((free_gib - reserve_gib) // per_runner_gib) if free_gib > reserve_gib else 0
    return max(0, min(slots, runners + extra))


def memory_decide(free_gib: float | None, runners: int, reason: str | None, mem: dict, slots: int,
                  failed: bool = False, ceiling: int | None = None) -> tuple[str, int | None]:
    """("set", cap) to write a memory cap, ("clear", None) to lift this tier's hold, ("keep", None)
    or ("foreign", None). A failed or empty read keeps everything (fail safe). A lower existing
    cap (CPU reserve) is never raised here; a memory hold re-caps up or down with the recover
    margin, and lifts once the margin clears the full slots. ceiling (the CPU reserve cap while
    the node's CPU is hot) bounds the cap and blocks a lift."""
    text = (reason or "").strip()
    if text and RESERVE_TAG not in " " + text:
        return "foreign", None
    if failed or free_gib is None:
        return "keep", None
    mem_held = MEM_TAG in text
    reserve = float(mem["reserve_gib"]) + (float(mem.get("recover_gib", 0)) if mem_held else 0.0)
    cap = memory_cap(free_gib, runners, slots, reserve, float(mem["per_runner_gib"]))
    current = hold_cap(text)
    if cap >= slots and ceiling is None:
        return ("clear", None) if mem_held else ("keep", None)
    if ceiling is not None:
        cap = min(cap, ceiling)
        if not mem_held and current is not None and cap >= current:
            return "keep", None
    if current is None or cap < current or (mem_held and cap != current):
        return "set", cap
    return "keep", None


def reserve_query(template: str, node: str) -> str:
    return template.replace("$NODE", node)


def memory_step(cfg: dict, res: dict, mem: dict, rnode: str, reason: str | None, now: dt.datetime,
                reserve_log: dict, log: dict, ceiling: int | None = None):
    """Run the memory tier for one node. Returns the new hold value (str, or None to clear) when
    the memory tier wants a write, else False. Logs under reserve_log[rnode]["memory"]."""
    failed, free, runners = False, None, 0
    try:
        raw = prom_scalar(cfg["prometheus_url"], reserve_query(mem["free_query"], rnode))
        free = None if raw is None else raw / 2 ** 30
    except (urllib.error.URLError, OSError, ValueError, KeyError, GuardError) as err:
        failed = True
        log["errors"].append("prometheus:reserve:%s:memory:%s" % (rnode, type(err).__name__))
    try:
        pods = kube("/api/v1/namespaces/%s/pods?%s" % (NAMESPACE, urllib.parse.urlencode(
            {"labelSelector": PROFILE_LABEL, "fieldSelector": "spec.nodeName=" + rnode})))
        runners = len(newest_first(pods.get("items", []), rnode))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
        failed = True
        log["errors"].append("kube:reserve:%s:memory:%s" % (rnode, type(err).__name__))
    slots = int((mem.get("slots") or {}).get(rnode, mem.get("default_slots", 20)))
    action, cap = memory_decide(free, runners, reason, mem, slots, failed, ceiling)
    reserve_log[rnode]["memory"] = {"free_gib": None if free is None else round(free, 2), "runners": runners,
                                    "slots": slots, "action": action, "cap": cap,
                                    "mode": mem.get("mode", "dry-run")}
    if action == "set":
        return "cap=%d %s%smem<%gGiB free=%.1fGiB" % (cap, iso(now), RESERVE_TAG, float(mem["reserve_gib"]), free)
    if action == "clear":
        return None
    return False


def newest_first(pods: list[dict], node: str) -> list[dict]:
    """Runner Pods on node that are not already terminating, newest first."""
    live = [p for p in pods
            if p.get("spec", {}).get("nodeName") == node
            and PROFILE_LABEL in (p.get("metadata", {}).get("labels") or {})
            and not p.get("metadata", {}).get("deletionTimestamp")
            and p.get("status", {}).get("phase") in ("Pending", "Running")]
    return sorted(live, key=lambda p: (p["metadata"].get("creationTimestamp", ""), p["metadata"]["name"]),
                  reverse=True)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------- I/O

def write_path(path: str, method: str, dry_run=False) -> str:
    """Add the write query: fieldManager on ConfigMap writes, dryRun=All on dry runs.

    Pod evictions keep no managedFields, so only ConfigMap writes carry the manager name.
    """
    params = []
    if method in ("POST", "PATCH", "PUT") and "/configmaps" in path:
        params.append("fieldManager=" + FIELD_MANAGER)
    if dry_run and method != "GET":
        params.append("dryRun=All")
    if not params:
        return path
    return path + ("&" if "?" in path else "?") + "&".join(params)


def kube(path: str, method: str = "GET", body=None, content_type: str = "application/json", dry_run=False,
         sleep=None):
    import time
    sleep = sleep or time.sleep
    attempts = KUBE_ATTEMPTS if method in ("GET", "PATCH", "PUT") else 1
    for attempt in range(attempts):
        try:
            return kube_once(path, method, body, content_type, dry_run)
        except urllib.error.HTTPError:
            raise
        except OSError:  # URLError, socket timeout, connection reset
            if attempt == attempts - 1:
                raise
            sleep(KUBE_RETRY_SLEEP)


def kube_once(path: str, method: str = "GET", body=None, content_type: str = "application/json", dry_run=False):
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    url = "https://%s:%s%s" % (host, port, write_path(path, method, dry_run))
    with open(TOKEN_PATH, encoding="utf-8") as stream:
        token = stream.read().strip()
    req = urllib.request.Request(url, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                                          "Content-Type": content_type})
    ctx = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def wait_network(base: str, budget: float, sleep=None, clock=None) -> float:
    """Poll Prometheus /-/ready until it answers or budget seconds pass; return seconds waited.

    A new Pod is not yet in kube-router's NetworkPolicy ipsets for its first few
    seconds (measured 2026-11-07: refused at t+0, open by t+10 s), so the first
    connections of a one-minute CronJob Pod are rejected. Never raises: if the
    budget runs out, the signal reads below fail closed as usual.
    """
    import time
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    start = clock()
    ok = 0
    while True:
        try:
            with urllib.request.urlopen(base.rstrip("/") + "/-/ready", timeout=3):
                ok += 1
        except (urllib.error.URLError, OSError, ValueError):
            ok = 0
        # The Service has several backends on different nodes and each node's
        # kube-router updates its ipsets on its own schedule: require a run of
        # successes, not one.
        if ok >= READY_RUN or clock() - start >= budget:
            return clock() - start
        sleep(1 if ok else 2)


def prom_scalar(base: str, query: str, sleep=None) -> float | None:
    import time
    sleep = sleep or time.sleep
    url = base.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    for attempt in range(PROM_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                doc = json.load(resp)
            break
        except (urllib.error.URLError, OSError):
            if attempt == PROM_ATTEMPTS - 1:
                raise
            sleep(1)
    if doc.get("status") != "success":
        raise GuardError("prometheus status " + str(doc.get("status")))
    result = doc["data"]["result"]
    if not result:
        return None
    values = [float(r["value"][1]) for r in result]
    values = [v for v in values if v == v]  # drop NaN
    return max(values) if values else None


def read_hold(node: str) -> tuple[dict | None, dict | None]:
    """(configmap or None if absent, parsed hold for node or None)."""
    try:
        cm = kube("/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, HOLD_CM))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None, None
        raise
    reason = ((cm.get("data") or {}).get(node) or "").strip()
    if not reason:
        return cm, None
    ann = cm.get("metadata", {}).get("annotations") or {}
    return cm, {"reason": reason, "since": parse_iso(ann.get(SINCE)), "last_trip": parse_iso(ann.get(LAST_TRIP)),
                "cap": hold_cap(reason)}


def write_hold(cm, node: str, reason: str | None, now: dt.datetime, held: dict | None, dry: bool,
               soft: bool = False):
    """reason None clears the node entry. Annotations carry the timers. A soft write
    keeps last-trip (the hard clean timer) untouched."""
    if reason is None:
        ann = {SINCE: None, LAST_TRIP: None}
    elif soft:
        ann = {SINCE: iso(held["since"]) if held and held.get("since") else iso(now)}
    else:
        # A trip or a blind (missing-signal) run both restart the clean timer.
        ann = {SINCE: iso(held["since"]) if held and held.get("since") else iso(now), LAST_TRIP: iso(now)}
    if cm is None:
        if reason is None:
            return
        body = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": HOLD_CM, "namespace": NAMESPACE,
                             "labels": {"app.kubernetes.io/name": "ci-runner-hot-guard",
                                        "app.kubernetes.io/part-of": "estate-ci"},
                             "annotations": {k: v for k, v in ann.items() if v}},
                "data": {node: reason}}
        kube("/api/v1/namespaces/%s/configmaps" % NAMESPACE, "POST", body, dry_run=dry)
        return
    kube("/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, HOLD_CM), "PATCH",
         {"metadata": {"annotations": ann}, "data": {node: reason}},
         content_type="application/merge-patch+json", dry_run=dry)


def evict(name: str, dry: bool):
    body = {"apiVersion": "policy/v1", "kind": "Eviction",
            "metadata": {"name": name, "namespace": NAMESPACE}}
    if dry:
        body["deleteOptions"] = {"dryRun": ["All"]}
    kube("/api/v1/namespaces/%s/pods/%s/eviction" % (NAMESPACE, name), "POST", body)


def main() -> int:
    with open(CONFIG_PATH, encoding="utf-8") as stream:
        cfg = json.load(stream)
    mode = os.environ.get("GUARD_MODE") or cfg.get("mode", "enforce")
    dry = mode != "enforce"
    node = cfg["node"]
    now = dt.datetime.now(dt.timezone.utc)
    log = {"ts": iso(now), "guard": "ci-runner-hot-guard", "mode": mode, "node": node,
           "values": {}, "errors": []}

    values: dict = {}
    prom_failed = False
    log["net_wait_s"] = round(wait_network(cfg["prometheus_url"], float(cfg.get("net_wait_seconds", 25))), 1)
    queries = dict((name, spec["query"]) for name, spec in cfg["signals"].items())
    if cfg.get("soft_recover"):
        queries["soft_recover"] = cfg["soft_recover"]["query"]
    for name, query in queries.items():
        try:
            values[name] = prom_scalar(cfg["prometheus_url"], query)
        except (urllib.error.URLError, OSError, ValueError, KeyError, GuardError) as err:
            prom_failed = True
            values[name] = None
            log["errors"].append("prometheus:%s:%s" % (name, type(err).__name__))
    log["values"] = values
    tripped_all, missing = evaluate(cfg["signals"], values)
    soft = [n for n in tripped_all if cfg["signals"][n].get("tier", "hard") == "soft"]
    tripped = [n for n in tripped_all if n not in soft]
    recovered = False
    rec = cfg.get("soft_recover")
    if rec and values.get("soft_recover") is not None:
        recovered = bool(evaluate({"r": rec}, {"r": values["soft_recover"]})[0])
    soft_cap = int(cfg.get("soft_cap", 0))
    force = os.environ.get("GUARD_FORCE_TRIP", "").strip()
    if force:
        tripped.append("forced-" + "".join(c for c in force if c.isalnum() or c in "-_")[:40])

    rc = 0
    try:
        cm, held = read_hold(node)
        decision = decide(now, tripped, missing, held, int(cfg.get("clean_seconds", 900)), soft, recovered)
        if force:
            decision["evict"] = True  # an operator's forced trip is confirmed by definition
        log.update(decision=decision, held_before=bool(held))
        if decision["hold"] == "set":
            write_hold(cm, node, "%s %s" % (iso(now), decision["reason"]), now, held, dry)
        elif decision["hold"] == "soft":
            write_hold(cm, node, "cap=%d %s %s" % (soft_cap, iso(now), decision["reason"]), now, held, dry, soft=True)
        elif decision["hold"] == "clear":
            write_hold(cm, node, None, now, held, dry)
        evicted = []
        if decision["evict"]:
            pods = kube("/api/v1/namespaces/%s/pods?%s" % (NAMESPACE, urllib.parse.urlencode(
                {"labelSelector": PROFILE_LABEL, "fieldSelector": "spec.nodeName=" + node})))
            for pod in newest_first(pods.get("items", []), node)[: int(cfg.get("evict_per_run", 2))]:
                name = pod["metadata"]["name"]
                try:
                    evict(name, dry)
                    evicted.append(name)
                except urllib.error.HTTPError as err:
                    log["errors"].append("evict:%s:http%d" % (name, err.code))
                    rc = 1
        log["evicted"] = evicted
    except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
        log["errors"].append("kube:%s" % type(err).__name__)
        rc = 1
    res = cfg.get("reserve")
    if res:
        reserve_log = {}
        try:
            cm = kube("/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, HOLD_CM))
        except urllib.error.HTTPError as err:
            cm = None
            if err.code != 404:
                log["errors"].append("kube:reserve:http%d" % err.code)
                rc = 1
                res = None
        except (urllib.error.URLError, OSError, ValueError) as err:
            log["errors"].append("kube:reserve:%s" % type(err).__name__)
            rc, res = 1, None
        for rnode in (res or {}).get("nodes", []):
            got, failed = {"coverage": 1.0}, False
            for key in ("trip", "recover", "coverage"):
                if key not in res:
                    continue
                try:
                    got[key] = prom_scalar(cfg["prometheus_url"], reserve_query(res[key]["query"], rnode))
                except (urllib.error.URLError, OSError, ValueError, KeyError, GuardError) as err:
                    prom_failed = failed = True
                    got[key] = None
                    log["errors"].append("prometheus:reserve:%s:%s:%s" % (rnode, key, type(err).__name__))
            reason = ((cm or {}).get("data") or {}).get(rnode)
            action = reserve_decide(got["trip"], got["recover"], reason, res["trip"], res["recover"],
                                    got["coverage"], res.get("coverage"), failed)
            reserve_log[rnode] = {"busy": got["trip"], "calm": got["recover"], "coverage": got["coverage"],
                                  "action": action}
            recap = (reserve_recap(reason, reserve_cap(res, rnode))
                     if action == "keep" and not failed and MEM_TAG not in (reason or "") else None)
            # Raising a held cap waits until the node is below the trip line; lowering applies at once.
            if recap is not None and hold_cap(recap) > (hold_cap(reason) or 0) and (
                    got["trip"] is None or evaluate({"t": res["trip"]}, {"t": got["trip"]})[0]):
                recap = None
            if recap is not None:
                action = reserve_log[rnode]["action"] = "recap"
            if action == "clear" and MEM_TAG in (reason or ""):
                # A memory hold is lifted by the memory tier only (estate-ci#248).
                action = reserve_log[rnode]["action"] = "keep"
            mem = res.get("memory")
            if mem and action == "keep":
                hot = got["trip"] is None or bool(evaluate({"t": res["trip"]}, {"t": got["trip"]})[0])
                mem_value = memory_step(cfg, res, mem, rnode, reason, now, reserve_log, log,
                                        reserve_cap(res, rnode) if hot else None)
                if mem_value is not False:
                    action, recap = "recap", mem_value
                    if mem.get("mode", "dry-run") != "enforce":
                        continue
                    reserve_log[rnode]["action"] = "memory"
            if action not in ("set", "clear", "recap"):
                continue
            value = ("cap=%d %s%sbusy>%g" % (reserve_cap(res, rnode), iso(now), RESERVE_TAG, res["trip"]["threshold"])
                     if action == "set" else recap)
            try:
                if cm is None:
                    cm = {"metadata": {"annotations": {}}, "data": {}}
                    kube("/api/v1/namespaces/%s/configmaps" % NAMESPACE, "POST",
                         {"apiVersion": "v1", "kind": "ConfigMap",
                          "metadata": {"name": HOLD_CM, "namespace": NAMESPACE,
                                       "labels": {"app.kubernetes.io/name": "ci-runner-hot-guard",
                                                  "app.kubernetes.io/part-of": "estate-ci"}},
                          "data": {rnode: value}}, dry_run=dry)
                else:
                    kube("/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, HOLD_CM), "PATCH",
                         {"metadata": {"annotations": {}}, "data": {rnode: value}},
                         content_type="application/merge-patch+json", dry_run=dry)
                if value is None:
                    (cm.get("data") or {}).pop(rnode, None)
                else:
                    cm.setdefault("data", {})[rnode] = value
            except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
                log["errors"].append("kube:reserve:%s:%s" % (rnode, type(err).__name__))
                rc = 1
        log["reserve"] = reserve_log
    if prom_failed:
        rc = 1
    print(json.dumps(log, sort_keys=True), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
