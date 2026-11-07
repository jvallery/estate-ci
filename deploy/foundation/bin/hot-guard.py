#!/usr/bin/env python3
"""estate-ci node8 hot guard (D-67). Runners always lose to Frigate.

Runs every minute as CronJob ci-runner-hot-guard in ci-runners. stdlib only.

Each run reads trip signals from Prometheus (the queries are in the config
ConfigMap). Signals have a tier (COORD ruling 2026-11-07T03:49Z: runners always
lose to Frigate, but node CPU alone must not kill in-flight jobs):

  HARD trip      -> (Frigate degraded or restarted, node memory) write a reason
                    under the node name in ConfigMap ci-runner-slot-hold, so the
                    agents arc-validation-pool reconciler advertises 0
                    agentfleet.vallery.net/ci-runner-slot on the node within
                    about a minute (no new runners land). If the previous run
                    had already recorded a HARD trip (confirmed), also evict up
                    to evict_per_run of the node's runner Pods, newest first;
                    each later run evicts more while the trip holds.
  signal missing -> hold the node at 0 (fail closed) but evict nothing.
  SOFT trip      -> (node CPU, Frigate clean) write "cap=<soft_cap> ..." so the
                    reconciler advertises min(desired, soft_cap). Nothing is
                    evicted; runners drain as their jobs finish.
  hard + clean   -> once no trip has been seen for clean_seconds, drop the hold
                    (or step down to the soft cap if the soft signal still trips).
  soft + calm    -> drop the cap once soft_recover holds (CPU max over 10 min
                    below 0.75). The reconciler then restores the desired slots.

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

def kube(path: str, method: str = "GET", body=None, content_type: str = "application/json", dry_run=False):
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    url = "https://%s:%s%s" % (host, port, path)
    if dry_run and method != "GET":
        url += ("&" if "?" in url else "?") + "dryRun=All"
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
    while True:
        try:
            with urllib.request.urlopen(base.rstrip("/") + "/-/ready", timeout=3):
                return clock() - start
        except (urllib.error.URLError, OSError, ValueError):
            if clock() - start >= budget:
                return clock() - start
            sleep(2)


def prom_scalar(base: str, query: str) -> float | None:
    url = base.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        doc = json.load(resp)
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
    if prom_failed:
        rc = 1
    print(json.dumps(log, sort_keys=True), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
