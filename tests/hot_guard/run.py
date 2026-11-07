#!/usr/bin/env python3
"""Unit tests for deploy/foundation/bin/hot-guard.py decision logic (no cluster access)."""
import datetime as dt
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("guard", ROOT / "deploy/foundation/bin/hot-guard.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
cfg = json.loads((ROOT / "deploy/foundation/hot-guard-config.json").read_text())
SIG = cfg["signals"]
NOW = dt.datetime(2026, 11, 7, 2, 0, tzinfo=dt.timezone.utc)
CALM = {"node_cpu_busy_4m": 0.80, "node_memory_used_2m": 0.46, "frigate_skipped_ratio_4m": 0.0,
        "frigate_decode_deficit_4m": 0.0, "frigate_started_age_seconds": 21000.0, "soft_recover": 0.82}
failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r} want {want!r}")


check("calm", g.evaluate(SIG, CALM), ([], []))
check("cpu", g.evaluate(SIG, dict(CALM, node_cpu_busy_4m=0.86))[0], ["node_cpu_busy_4m"])
check("cpu-at-threshold", g.evaluate(SIG, dict(CALM, node_cpu_busy_4m=0.85))[0], [])
check("cpu is soft, frigate+memory hard", sorted(n for n, s_ in SIG.items() if s_.get("tier") == "soft"), ["node_cpu_busy_4m"])
check("mem", g.evaluate(SIG, dict(CALM, node_memory_used_2m=0.9))[0], ["node_memory_used_2m"])
check("skip", g.evaluate(SIG, dict(CALM, frigate_skipped_ratio_4m=0.2))[0], ["frigate_skipped_ratio_4m"])
check("decode", g.evaluate(SIG, dict(CALM, frigate_decode_deficit_4m=0.3))[0], ["frigate_decode_deficit_4m"])
check("restart", g.evaluate(SIG, dict(CALM, frigate_started_age_seconds=60))[0], ["frigate_started_age_seconds"])
check("missing", g.evaluate(SIG, dict(CALM, frigate_skipped_ratio_4m=None)), ([], ["frigate_skipped_ratio_4m"]))

d = g.decide(NOW, ["frigate_skipped_ratio_4m"], [], None, 900)
check("first trip closes, no evict", (d["hold"], d["evict"]), ("set", False))
d = g.decide(NOW, ["frigate_skipped_ratio_4m"], [], {"reason": "2026 trip:x", "since": NOW, "last_trip": NOW, "cap": 0}, 900)
check("confirmed trip evicts", (d["hold"], d["evict"]), ("set", True))
d = g.decide(NOW, ["frigate_skipped_ratio_4m"], [], {"reason": "2026 missing:x", "since": NOW, "last_trip": NOW, "cap": 0}, 900)
check("trip after blind hold is unconfirmed", d["evict"], False)
d = g.decide(NOW, [], ["x"], None, 900)
check("missing holds, no evict", (d["hold"], d["evict"]), ("set", False))
check("clean unheld", g.decide(NOW, [], [], None, 900)["hold"], "none")
held = {"reason": "r", "since": NOW - dt.timedelta(minutes=30), "last_trip": NOW - dt.timedelta(minutes=14)}
check("cooling", g.decide(NOW, [], [], held, 900)["hold"], "keep")
held["last_trip"] = NOW - dt.timedelta(minutes=15)
check("restore after 15 clean", g.decide(NOW, [], [], held, 900)["hold"], "clear")
check("trip while held re-sets", g.decide(NOW, ["x"], [], held, 900)["hold"], "set")
# Two tiers (COORD 2026-11-07T03:49Z).
check("hold_cap", [g.hold_cap(x) for x in ("", "  ", "2026 trip:x", "cap=4 2026 soft:x", "cap=x y")], [None, None, 0, 4, 0])
d = g.decide(NOW, [], [], None, 900, ["node_cpu_busy_4m"])
check("soft trip caps, no evict", (d["hold"], d["evict"]), ("soft", False))
soft_held = {"reason": "cap=4 r", "since": NOW, "last_trip": None, "cap": 4}
check("soft held + soft trip keeps", g.decide(NOW, [], [], soft_held, 900, ["node_cpu_busy_4m"])["hold"], "keep")
check("soft held, not recovered keeps", g.decide(NOW, [], [], soft_held, 900, [], False)["hold"], "keep")
check("soft held, recovered clears", g.decide(NOW, [], [], soft_held, 900, [], True)["hold"], "clear")
d = g.decide(NOW, ["frigate_skipped_ratio_4m"], [], soft_held, 900, ["node_cpu_busy_4m"])
check("hard beats soft cap (first run closes only)", (d["hold"], d["evict"]), ("set", False))
hard_held = {"reason": "r", "since": NOW - dt.timedelta(minutes=30), "last_trip": NOW - dt.timedelta(minutes=20), "cap": 0}
check("hard clean + soft steps down to cap", g.decide(NOW, [], [], hard_held, 900, ["node_cpu_busy_4m"])["hold"], "soft")
hard_held["last_trip"] = NOW - dt.timedelta(minutes=5)
check("hard cooling ignores soft", g.decide(NOW, [], [], hard_held, 900, ["node_cpu_busy_4m"])["hold"], "keep")


def pod(name, ts, node="vm-k3s-node8", label=True, phase="Running", deleting=False):
    md = {"name": name, "creationTimestamp": ts, "labels": {g.PROFILE_LABEL: "ci"} if label else {}}
    if deleting:
        md["deletionTimestamp"] = ts
    return {"metadata": md, "spec": {"nodeName": node}, "status": {"phase": phase}}


pods = [pod("old", "2026-11-07T01:00:00Z"), pod("new", "2026-11-07T01:30:00Z"),
        pod("mid", "2026-11-07T01:10:00Z", phase="Pending"), pod("other-node", "2026-11-07T01:40:00Z", node="vm-k3s-node4"),
        pod("guard", "2026-11-07T01:50:00Z", label=False), pod("dying", "2026-11-07T01:55:00Z", deleting=True),
        pod("done", "2026-11-07T01:56:00Z", phase="Succeeded")]
check("newest first", [p["metadata"]["name"] for p in g.newest_first(pods, "vm-k3s-node8")], ["new", "mid", "old"])
check("iso roundtrip", g.parse_iso(g.iso(NOW)), NOW)
check("bad iso", g.parse_iso("junk"), None)

# main() wiring with a fake API server and Prometheus.
import os, tempfile, urllib.error, io
state = {"cm": None, "calls": []}


def fake_kube(path, method="GET", body=None, content_type="application/json", dry_run=False):
    state["calls"].append((method, path.split("?")[0], dry_run))
    if path.endswith("/configmaps/" + g.HOLD_CM) and method == "GET":
        if state["cm"] is None:
            raise urllib.error.HTTPError(path, 404, "nf", {}, io.BytesIO(b""))
        return state["cm"]
    if path.endswith("/configmaps") and method == "POST":
        state["cm"] = body
        return body
    if path.endswith("/configmaps/" + g.HOLD_CM) and method == "PATCH":
        cm = state["cm"]
        for k, v in body["data"].items():
            if v is None:
                cm["data"].pop(k, None)
            else:
                cm["data"][k] = v
        for k, v in body["metadata"]["annotations"].items():
            if v is None:
                cm["metadata"]["annotations"].pop(k, None)
            else:
                cm["metadata"]["annotations"][k] = v
        return cm
    if "/pods?" in path:
        return {"items": pods}
    if path.endswith("/eviction"):
        return {}
    raise AssertionError(path)


g.wait_network = lambda base, budget: 0.0
VALUES = dict(CALM)
QUERIES = dict({n: s_["query"] for n, s_ in SIG.items()}, soft_recover=cfg["soft_recover"]["query"])
g.kube = fake_kube
g.prom_scalar = lambda base, q: VALUES[[n for n, q_ in QUERIES.items() if q_ == q][0]]
cfgfile = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump(cfg, cfgfile); cfgfile.close()
g.CONFIG_PATH = cfgfile.name

check("calm run rc", g.main(), 0)
check("calm: no hold cm", state["cm"], None)
VALUES["frigate_skipped_ratio_4m"] = 0.5
check("trip run rc", g.main(), 0)
check("trip: node held", "vm-k3s-node8" in state["cm"]["data"], True)
check("first trip: no eviction", [c for c in state["calls"] if c[1].endswith("/eviction")], [])
check("confirmed trip run rc", g.main(), 0)
ev = [c[1].split("/")[-2] for c in state["calls"] if c[1].endswith("/eviction")]
check("trip: evicts 2 newest", ev, ["new", "mid"])
VALUES["frigate_skipped_ratio_4m"] = 0.0
state["calls"].clear()
check("cooling rc", g.main(), 0)
check("cooling: still held, no eviction", ("vm-k3s-node8" in state["cm"]["data"], [c for c in state["calls"] if c[1].endswith("/eviction")]), (True, []))
state["cm"]["metadata"]["annotations"][g.LAST_TRIP] = "2026-01-01T00:00:00Z"
check("restore rc", g.main(), 0)
check("restore: hold cleared", state["cm"]["data"], {})
VALUES["node_cpu_busy_4m"] = 0.9
state["calls"].clear()
check("soft run rc", g.main(), 0)
check("soft: cap=4 written, no eviction", (state["cm"]["data"]["vm-k3s-node8"].split()[0], [c for c in state["calls"] if c[1].endswith("/eviction")]), ("cap=4", []))
VALUES["node_cpu_busy_4m"] = 0.7
check("soft cooling rc", g.main(), 0)
check("soft: still capped while recover fails", state["cm"]["data"]["vm-k3s-node8"].split()[0], "cap=4")
VALUES["soft_recover"] = 0.7
check("soft recover rc", g.main(), 0)
check("soft: cap dropped", state["cm"]["data"], {})
VALUES.update(CALM)
g.prom_scalar = lambda base, q: (_ for _ in ()).throw(urllib.error.URLError("down"))
state["calls"].clear()
check("prom down rc", g.main(), 1)
check("prom down: fail closed, no eviction", ("vm-k3s-node8" in state["cm"]["data"], [c for c in state["calls"] if c[1].endswith("/eviction")]), (True, []))
os.environ["GUARD_MODE"] = "dry-run"
state["cm"]["data"] = {}
g.prom_scalar = lambda base, q: VALUES[[n for n, q_ in QUERIES.items() if q_ == q][0]]
os.environ["GUARD_FORCE_TRIP"] = "test"
state["calls"].clear()
g.kube = lambda path, method="GET", body=None, content_type="application/json", dry_run=False: (
    state["calls"].append((method, path.split("?")[0], dry_run, body)) or fake_kube(path, method, body, content_type, dry_run)
    if method == "GET" or "/pods?" in path else state["calls"].append(("W", path, dry_run, body)) or {})
check("forced dry-run rc", g.main(), 0)
writes = [c for c in state["calls"] if c[0] == "W"]
check("dry-run: every write is dry", all(c[2] or (c[3] or {}).get("deleteOptions") == {"dryRun": ["All"]} for c in writes) and len(writes) == 3, True)
os.environ.pop("GUARD_MODE"); os.environ.pop("GUARD_FORCE_TRIP")

# wait_network: polls /-/ready through kube-router's new-Pod ipset lag, never raises.
import urllib.request as _ur
_t = [0.0]
def _ready_after_6s(url, timeout):
    if _t[0] < 6:
        raise urllib.error.URLError("refused")
    return io.BytesIO(b"ok")
_orig = _ur.urlopen
_ur.urlopen = _ready_after_6s
spec2 = importlib.util.spec_from_file_location("guard2", ROOT / "deploy/foundation/bin/hot-guard.py")
g2 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(g2)
_tick = lambda d: _t.__setitem__(0, _t[0] + d)
check("wait_network opens after lag", g2.wait_network("http://p", 25, sleep=_tick, clock=lambda: _t[0]), 6.0)
_t[0] = 0.0
_ur.urlopen = lambda url, timeout: (_ for _ in ()).throw(urllib.error.URLError("x"))
check("wait_network gives up at budget", g2.wait_network("http://p", 5, sleep=_tick, clock=lambda: _t[0]), 6.0)
_ur.urlopen = _orig

if failures:
    sys.exit(f"hot-guard tests: {failures} failure(s)")
print("hot-guard tests: OK")
