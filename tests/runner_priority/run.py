#!/usr/bin/env python3
"""Self-test for runner priority tiers in scripts/render_runners.py (estate-ci#249).

1. runners.yaml renders and exactly its gate_sets use ci-gate; with gate_sets
   cleared no set uses ci-gate.
2. A set named in gate_sets renders with ci-gate; other sets keep their tier.
3. gate_sets naming an unknown set is refused.
4. A set in both gate_sets and incident_sets is refused.
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
base = yaml.safe_load((root / "runners.yaml").read_text())
failed = 0


def classes(out):
    got = {}
    for fname, text in out.items():
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, dict) or doc.get("kind") != "Application":
                continue
            helm = (doc.get("spec", {}).get("source", {}) or {}).get("helm", {}) or {}
            values = helm.get("valuesObject") or yaml.safe_load(helm.get("values") or "{}") or {}
            tpl = (values.get("template") or {}).get("spec") or {}
            if "runnerScaleSetName" in values:
                got[values["runnerScaleSetName"]] = tpl.get("priorityClassName")
    return got


def check(name, ok, cond=True):
    global failed
    if not (ok and cond):
        failed += 1
        print(f"FAIL {name}")


def render(cfg):
    try:
        return rr.render(cfg), None
    except rr.RenderError as exc:
        return None, str(exc)


real_gate = list((base.get("defaults") or {}).get("gate_sets") or [])
out, err = render(copy.deepcopy(base))
check("runners.yaml renders", out is not None)
real = classes(out) if out else {}
check("designated gate sets use ci-gate", all(real.get(n) == "ci-gate" for n in real_gate))
check("only designated gate sets use ci-gate", all(n in real_gate for n, c in real.items() if c == "ci-gate"))

base["defaults"]["gate_sets"] = []
out, err = render(copy.deepcopy(base))
check("renders with gate_sets empty", out is not None)
plain = classes(out)
check("rendered sets found", len(plain) > 10)
check("no ci-gate while gate_sets is empty", "ci-gate" not in plain.values())

target = sorted(n for n, c in plain.items() if c == "ci-preemptible-infra")[0]
cfg = copy.deepcopy(base)
cfg["defaults"]["gate_sets"] = [target]
out, err = render(cfg)
check("gate set renders", out is not None)
gated = classes(out) if out else {}
check(f"{target} uses ci-gate", gated.get(target) == "ci-gate")
check("other sets unchanged", all(gated.get(n) == c for n, c in plain.items() if n != target))

cfg = copy.deepcopy(base)
cfg["defaults"]["gate_sets"] = ["no-such-set-ci"]
out, err = render(cfg)
check("unknown gate set refused", out is None and "gate_sets names unknown" in (err or ""))

cfg = copy.deepcopy(base)
cfg["defaults"]["gate_sets"] = [target]
cfg["defaults"]["incident_sets"] = [target]
out, err = render(cfg)
check("gate+incident overlap refused", out is None and "both incident_sets and gate_sets" in (err or ""))

if failed:
    sys.exit(f"runner priority self-test: {failed} failure(s)")
print("runner priority self-test: ok")
