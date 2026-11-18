#!/usr/bin/env python3
"""Self-test for runner scratch volumes in scripts/render_runners.py (estate-ci#101).

1. scratch: pvc renders work, exchange (and pg) as generic ephemeral volumes on the disposable
   Longhorn class with the emptyDir size as the claim size; emptyDir stays the default.
2. An unknown scratch mode is refused.
3. Every rendered set whose runners.yaml entry says scratch: pvc carries no emptyDir work,
   exchange or pg volume, so an emptyDir scratch volume cannot come back for a flipped set.
4. defaults.scratch_pvc_exclude_nodes renders a required hostname NotIn term on scratch: pvc sets
   only; emptyDir sets and an empty list get none.
"""
import importlib.util
import sys
from pathlib import Path

import re
import yaml

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rr", root / "scripts/render_runners.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)
failed = 0
SCRATCH = ("work", "exchange", "pg")


def check(name, got, want):
    global failed
    if got != want:
        failed += 1
        print(f"FAIL {name}: got {got!r} want {want!r}")


pvc = rr.scratch_volume("work", "16Gi", {"scratch": "pvc"}, "fixture")
tmpl = pvc["ephemeral"]["volumeClaimTemplate"]["spec"]
check("pvc class", tmpl["storageClassName"], "longhorn-v1-disposable-cache-rwo")
check("pvc size", tmpl["resources"]["requests"]["storage"], "16Gi")
check("pvc access", tmpl["accessModes"], ["ReadWriteOnce"])
check("pvc has no emptyDir", "emptyDir" in pvc, False)
check("default emptyDir", rr.scratch_volume("work", "16Gi", {}, "fixture"), {"name": "work", "emptyDir": {"sizeLimit": "16Gi"}})
try:
    rr.scratch_volume("work", "16Gi", {"scratch": "hostPath"}, "fixture")
    check("unknown mode refused", "accepted", "refused")
except rr.RenderError:
    pass

# 4. defaults.scratch_pvc_exclude_nodes: a required NotIn term for scratch: pvc sets only.
d_ex = {"scratch_pvc_exclude_nodes": ["vm-k3s-node4"]}
want_ex = {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [{"matchExpressions": [{
    "key": "kubernetes.io/hostname", "operator": "NotIn", "values": ["vm-k3s-node4"]}]}]}}
check("exclusion on pvc set", rr.scratch_pvc_exclusion(d_ex, {"scratch": "pvc"}), want_ex)
check("no exclusion on emptyDir set", rr.scratch_pvc_exclusion(d_ex, {}), {})
check("no exclusion when list empty", rr.scratch_pvc_exclusion({"scratch_pvc_exclude_nodes": []}, {"scratch": "pvc"}), {})

cfg = yaml.safe_load((root / "runners.yaml").read_text())
flipped = {}
for entry in cfg["sets"]:
    # Same normalisation as render_runners.py (AI_presentation -> ai-presentation).
    base = re.sub(r"[^a-z0-9-]", "-", entry["repo"].split("/")[1].lower())
    for profile, opts in (entry.get("profiles") or {}).items():
        if isinstance(opts, dict) and opts.get("scratch") == "pvc":
            flipped[f"{base}-{rr.SUFFIX.get(profile, profile)}"] = profile
if not flipped:
    check("at least one scratch: pvc set", 0, ">=1")
for name in sorted(flipped):
    app = root / "deploy/runners/apps" / f"{name}.yaml"
    text = app.read_text()
    vols = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("volumes"), list):
                vols.extend(v for v in node["volumes"] if isinstance(v, dict) and "name" in v)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and "volumes:" in node:
            walk(yaml.safe_load(node))

    for doc in yaml.safe_load_all(text):
        walk(doc)
    for v in vols:
        if v["name"] in SCRATCH:
            check(f"{name} {v['name']} is ephemeral", "ephemeral" in v and "emptyDir" not in v, True)
    check(f"{name} renders scratch volumes", sum(v["name"] in SCRATCH for v in vols) >= 2, True)

# 5. set_spread (pi#1036): a per-set soft hostname spread on top of the all-runner one, keyed on
#    the set label; sets without it keep the single D-87 spread and no set label.
D = yaml.safe_load((root / "runners.yaml").read_text())["defaults"]
sp = rr.pod_template(D, "ci", {"set_spread": True}, "o/x", "x", "ci", set_name="x-ci")
tsc = sp["spec"]["topologySpreadConstraints"]
check("set_spread constraints", len(tsc), 2)
check("set_spread selector", tsc[1]["labelSelector"], {"matchLabels": {"estate-ci.vallery.net/set": "x-ci"}})
check("set_spread soft", tsc[1]["whenUnsatisfiable"], "ScheduleAnyway")
check("set_spread key", tsc[1]["topologyKey"], "topology.vallery.net/proxmox-host")
check("set_spread label", sp["metadata"]["labels"].get("estate-ci.vallery.net/set"), "x-ci")
plain = rr.pod_template(D, "ci", {}, "o/x", "x", "ci", set_name="x-ci")
check("no set_spread constraints", len(plain["spec"]["topologySpreadConstraints"]), 1)
check("no set_spread label", "estate-ci.vallery.net/set" in plain["metadata"]["labels"], False)

# 6. No rendered set may carry two topologySpreadConstraints with the same merge key
#    (topologyKey, whenUnsatisfiable): server-side apply refuses the Application
#    (estate-platform#978 ComparisonError, 2026-11-18).
for app in sorted((root / "deploy/runners/apps").glob("*.yaml")):
    def find(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "topologySpreadConstraints":
                    yield v
                else:
                    yield from find(v)
        elif isinstance(node, list):
            for v in node:
                yield from find(v)
        elif isinstance(node, str) and "topologySpreadConstraints:" in node:
            yield from find(yaml.safe_load(node))
    for doc in yaml.safe_load_all(app.read_text()):
        for tsc_list in find(doc):
            keys = [(c["topologyKey"], c["whenUnsatisfiable"]) for c in tsc_list]
            check(f"{app.name} spread merge keys unique", len(keys), len(set(keys)))

if failed:
    print(f"runner_scratch: {failed} failure(s)")
    sys.exit(1)
print(f"runner_scratch: OK ({len(flipped)} set(s) on scratch: pvc)")
