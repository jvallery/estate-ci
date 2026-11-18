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
# Per-node reserve (estate-ci#69): cap a hot node4-7 at reserve.cap, never evict, never touch other holds.
RES = cfg["reserve"]
check("reserve nodes are node4-7", RES["nodes"], ["vm-k3s-node4", "vm-k3s-node5", "vm-k3s-node6", "vm-k3s-node7"])
check("reserve cap below the STD-CI-10 20 slots", 0 < RES["cap"] < 20, True)
check("reserve trips before the 0.95 budget", RES["trip"]["threshold"] < 0.95 and RES["recover"]["threshold"] < RES["trip"]["threshold"], True)
check("reserve query per node", "$NODE" not in g.reserve_query(RES["trip"]["query"], "vm-k3s-node5")
      and 'nodename="vm-k3s-node5"' in g.reserve_query(RES["trip"]["query"], "vm-k3s-node5"), True)
rd = lambda busy, calm, reason=None: g.reserve_decide(busy, calm, reason, RES["trip"], RES["recover"])
RHELD = "cap=10 2026-11-10T00:00:00Z reserve:busy>0.92"
check("reserve calm unheld", rd(0.5, 0.5), "keep")
check("reserve hot sets", rd(0.95, 0.95), "set")
check("reserve at threshold does not set", rd(0.92, 0.95), "keep")
check("reserve hot + held keeps", rd(0.95, 0.95, RHELD), "keep")
check("reserve held, not calm keeps", rd(0.88, 0.88, RHELD), "keep")
check("reserve recover below 0.75 over 30 min", (RES["recover"]["threshold"], "[30m:" in RES["recover"]["query"]), (0.75, True))
check("reserve held, 0.8 is not calm", rd(0.5, 0.8, RHELD), "keep")
check("reserve held, calm clears", rd(0.5, 0.7, RHELD), "clear")
check("reserve empty signal is hot", (rd(None, 0.5, RHELD), rd(0.5, None), rd(None, 0.5)), ("keep", "set", "set"))
# Per-node reserve caps (COORD-W2 2026-11-17T20:36:50Z): node4-7 all cap 13 (52 slots, evenly spread).
check("reserve per-node caps", [g.reserve_cap(RES, n) for n in RES["nodes"]], [13, 13, 13, 13])
check("reserve per-node caps stay below 20", all(0 < g.reserve_cap(RES, n) < 20 for n in RES["nodes"]), True)
check("reserve recap a held node to its cap", g.reserve_recap(RHELD, 16), "cap=16 2026-11-10T00:00:00Z reserve:busy>0.92")
check("reserve recap no-op at the same cap", g.reserve_recap(RHELD, 10), None)
check("reserve recap leaves foreign and empty holds", (g.reserve_recap("cap=4 2026-11-10T00:00:00Z soft:node_cpu_busy_4m", 16), g.reserve_recap(None, 16), g.reserve_recap("", 16)), (None, None, None))
rdc = lambda cov, reason=None, failed=False: g.reserve_decide(0.5, 0.5, reason, RES["trip"], RES["recover"], cov, RES["coverage"], failed)
check("reserve partial samples are hot", (rdc(0.88), rdc(0.88, RHELD), rdc(None), rdc(0.97, RHELD)), ("set", "keep", "set", "clear"))
check("reserve partial samples block a clear", g.reserve_decide(0.5, 0.5, RHELD, RES["trip"], RES["recover"], 0.9, RES["coverage"]), "keep")
check("reserve failed query writes nothing", (rdc(None, failed=True), g.reserve_decide(None, None, RHELD, RES["trip"], RES["recover"], None, RES["coverage"], True)), ("keep", "keep"))
check("reserve leaves a foreign hold", (rd(0.5, 0.5, "2026 trip:x"), rd(0.95, 0.95, "cap=4 2026 soft:x")), ("foreign", "foreign"))


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
for _n in cfg["reserve"]["nodes"]:
    for _k in ("trip", "recover", "coverage"):
        QUERIES["reserve_%s:%s" % (_k, _n)] = g.reserve_query(cfg["reserve"][_k]["query"], _n)
        VALUES["reserve_%s:%s" % (_k, _n)] = 1.0 if _k == "coverage" else 0.5
    QUERIES["reserve_memfree:%s" % _n] = g.reserve_query(cfg["reserve"]["memory"]["free_query"], _n)
    VALUES["reserve_memfree:%s" % _n] = 60.0 * 2 ** 30
CALM = dict(VALUES)
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
VALUES["reserve_trip:vm-k3s-node5"] = VALUES["reserve_recover:vm-k3s-node5"] = 0.95
state["cm"]["data"]["vm-k3s-node6"] = "2026 trip:operator"
state["calls"].clear()
check("reserve hot run rc", g.main(), 0)
check("reserve: node5 capped at its 13, node6 hold kept, no eviction",
      (state["cm"]["data"]["vm-k3s-node5"].split()[0], state["cm"]["data"]["vm-k3s-node6"], "vm-k3s-node8" in state["cm"]["data"],
       [c for c in state["calls"] if c[1].endswith("/eviction")]), ("cap=13", "2026 trip:operator", False, []))
VALUES["reserve_trip:vm-k3s-node5"] = 0.88
check("reserve cooling rc", g.main(), 0)
check("reserve: still capped while recover fails", state["cm"]["data"]["vm-k3s-node5"].split()[0], "cap=13")
VALUES["reserve_trip:vm-k3s-node5"] = 0.5
VALUES["reserve_recover:vm-k3s-node5"] = 0.8
check("reserve not calm under 0.75 rc", g.main(), 0)
check("reserve: still capped at recover 0.8", state["cm"]["data"]["vm-k3s-node5"].split()[0], "cap=13")
# A node held at the old cap (10) is re-capped to its configured 13 only when below the trip line.
state["cm"]["data"]["vm-k3s-node5"] = "cap=10 2026-11-17T05:18:04Z reserve:busy>0.92"
VALUES["reserve_trip:vm-k3s-node5"] = 0.95
check("reserve recap hot rc", g.main(), 0)
check("reserve recap: hot node keeps 10", state["cm"]["data"]["vm-k3s-node5"], "cap=10 2026-11-17T05:18:04Z reserve:busy>0.92")
VALUES["reserve_trip:vm-k3s-node5"] = 0.5
check("reserve recap calm rc", g.main(), 0)
check("reserve recap: calm node re-capped to 13, timestamp kept", state["cm"]["data"]["vm-k3s-node5"], "cap=13 2026-11-17T05:18:04Z reserve:busy>0.92")
VALUES["reserve_recover:vm-k3s-node5"] = 0.5
check("reserve recover rc", g.main(), 0)
check("reserve: cap dropped, foreign hold kept", state["cm"]["data"], {"vm-k3s-node6": "2026 trip:operator"})
state["cm"]["data"] = {}
_prom = g.prom_scalar
g.prom_scalar = lambda base, q: None if "vm-k3s-node7" in q else _prom(base, q)
check("reserve missing signal rc", g.main(), 0)
check("reserve missing signal: node7 capped as hot", (list(state["cm"]["data"]), state["cm"]["data"]["vm-k3s-node7"].split()[0]), (["vm-k3s-node7"], "cap=13"))
state["cm"]["data"] = {}
g.prom_scalar = lambda base, q: (_ for _ in ()).throw(urllib.error.URLError("x")) if "vm-k3s-node7" in q else _prom(base, q)
check("reserve failed query rc", g.main(), 1)
check("reserve failed query: no write", state["cm"]["data"], {})
VALUES["reserve_coverage:vm-k3s-node6"] = 0.85
check("reserve partial coverage rc", g.main() in (0, 1), True)
check("reserve partial coverage: node6 capped", state["cm"]["data"].get("vm-k3s-node6", "").split()[:1], ["cap=13"])
VALUES["reserve_coverage:vm-k3s-node6"] = 1.0
state["cm"]["data"] = {}
g.prom_scalar = _prom
VALUES.update(CALM)
g.prom_scalar = lambda base, q: (_ for _ in ()).throw(urllib.error.URLError("down"))
state["calls"].clear()
check("prom down rc", g.main(), 1)
check("prom down: fail closed, no eviction", ("vm-k3s-node8" in state["cm"]["data"], [c for c in state["calls"] if c[1].endswith("/eviction")]), (True, []))
# estate-ci#248 end to end: node4 (1 runner) at 86/88 GiB requested.
state["cm"]["data"] = {}
g.prom_scalar = lambda base, q: VALUES[[n for n, q_ in QUERIES.items() if q_ == q][0]]
VALUES["reserve_memfree:vm-k3s-node4"] = 2.0 * 2 ** 30
check("memory dry-run rc", g.main(), 0)
check("memory dry-run: nothing written", state["cm"]["data"], {})
_mcfg = json.loads(json.dumps(cfg)); _mcfg["reserve"]["memory"]["mode"] = "enforce"
with open(cfgfile.name, "w") as _f:
    json.dump(_mcfg, _f)
check("memory enforce rc", g.main(), 0)
check("memory enforce: node4 capped at its 1 runner, tagged", state["cm"]["data"].get("vm-k3s-node4", "").split()[0::2][:2],
      ["cap=1", "reserve:mem<6GiB"])
state["cm"]["data"]["vm-k3s-node6"] = "cap=8 operator: COORD-W2 06:09:54Z"
VALUES["reserve_memfree:vm-k3s-node6"] = 1.0 * 2 ** 30
check("memory enforce operator rc", g.main(), 0)
check("memory enforce: operator hold untouched", state["cm"]["data"]["vm-k3s-node6"], "cap=8 operator: COORD-W2 06:09:54Z")
VALUES["reserve_memfree:vm-k3s-node4"] = 38.0 * 2 ** 30
check("memory raise rc", g.main(), 0)
check("memory enforce: node4 raised to 1 + (38-10)//2 at 50/88", state["cm"]["data"]["vm-k3s-node4"].split()[0], "cap=15")
VALUES["reserve_memfree:vm-k3s-node4"] = 60.0 * 2 ** 30
check("memory lift rc", g.main(), 0)
check("memory enforce: node4 hold lifted once 20 slots fit above the margin", "vm-k3s-node4" in state["cm"]["data"], False)
with open(cfgfile.name, "w") as _f:
    json.dump(cfg, _f)
VALUES.update(CALM)

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
check("wait_network opens after lag (needs a run of 4)", g2.wait_network("http://p", 25, sleep=_tick, clock=lambda: _t[0]), 9.0)
_t[0] = 0.0
_ur.urlopen = lambda url, timeout: (_ for _ in ()).throw(urllib.error.URLError("x"))
check("wait_network gives up at budget", g2.wait_network("http://p", 5, sleep=_tick, clock=lambda: _t[0]), 6.0)
_n = [0]
def _flaky(url, timeout):
    _n[0] += 1
    if _n[0] < 3:
        raise urllib.error.URLError("refused")
    return io.BytesIO(b'{"status":"success","data":{"result":[{"value":[0,"0.5"]}]}}')
_ur.urlopen = _flaky
check("prom_scalar retries a refused backend", g2.prom_scalar("http://p", "up", sleep=lambda d: None), 0.5)
_ur.urlopen = _orig

# estate-ci#248: memory-headroom cap (pure logic; fake node allocatable 88 GiB, 20 slots)
MEM = cfg["reserve"]["memory"]
check("memory config ships dry-run", MEM["mode"], "dry-run")
check("memory cap formula", g.memory_cap(10.0, 4, 20, 6, 2), 6)
check("memory cap floor at runners", g.memory_cap(2.0, 7, 20, 6, 2), 7)
a, c = g.memory_decide(88 - 86, 7, None, MEM, 20)
check("86/88 requested caps at or below running", (a, c is not None and c <= 7), ("set", True))
held = "cap=7 2026-11-17T06:09:00Z reserve:mem<6GiB free=2.0GiB"
check("50/88 lifts once margin holds", g.memory_decide(88 - 50, 7, held, MEM, 20), ("clear", None))
check("hysteresis: just above reserve does not lift", g.memory_decide(12.5, 7, held, MEM, 20)[0], "set")
check("hysteresis re-cap uses recover margin", g.memory_decide(12.5, 7, held, MEM, 20)[1], 8)
check("unheld with room: keep", g.memory_decide(60, 3, None, MEM, 20), ("keep", None))
check("operator hold never rewritten", g.memory_decide(1.0, 9, "cap=8 operator: COORD 06:09Z", MEM, 20), ("foreign", None))
check("hard hold (no tag) never rewritten", g.memory_decide(1.0, 9, "2026-11-17T00:00:00Z trip:frigate", MEM, 20), ("foreign", None))
check("failed read changes nothing", g.memory_decide(1.0, 9, None, MEM, 20, failed=True), ("keep", None))
check("empty read changes nothing", g.memory_decide(None, 9, held, MEM, 20), ("keep", None))
cpu = "cap=10 2026-11-17T06:00:00Z reserve:busy>0.92"
check("memory lowers a CPU cap", g.memory_decide(4.0, 3, cpu, MEM, 20), ("set", 3))
check("memory never raises a CPU cap", g.memory_decide(60, 3, cpu, MEM, 20), ("keep", None))
check("CPU hot bounds a memory raise", g.memory_decide(60, 3, held, MEM, 20, ceiling=10), ("set", 10))
check("CPU hot blocks a memory lift", g.memory_decide(60, 10, "cap=10 x reserve:mem<6GiB", MEM, 20, ceiling=10), ("keep", None))
check("recap skips memory holds", g.reserve_recap(held, 10) is not None and g.MEM_TAG not in held, False)

# R21: ConfigMap writes carry the named field manager; evictions and reads do not.
CM = "/api/v1/namespaces/ci-runners/configmaps/ci-runner-slot-hold"
check("cm patch names the manager", g.write_path(CM, "PATCH"), CM + "?fieldManager=estate-ci-hot-guard")
check("cm create names the manager, dry run kept",
      g.write_path("/api/v1/namespaces/ci-runners/configmaps", "POST", dry_run=True),
      "/api/v1/namespaces/ci-runners/configmaps?fieldManager=estate-ci-hot-guard&dryRun=All")
check("cm read unchanged", g.write_path(CM, "GET"), CM)
EV = "/api/v1/namespaces/ci-runners/pods/x/eviction"
check("eviction has no manager", g.write_path(EV, "POST"), EV)
check("eviction dry run", g.write_path(EV, "POST", dry_run=True), EV + "?dryRun=All")

if failures:
    sys.exit(f"hot-guard tests: {failures} failure(s)")
print("hot-guard tests: OK")
