#!/usr/bin/env python3
"""Unit tests for deploy/foundation/bin/runner-slots.py (no cluster access)."""
import importlib.util
import io
import pathlib
import sys
import tempfile
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("slots", ROOT / "deploy/foundation/bin/runner-slots.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
RES = s.RESOURCE
failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r} want {want!r}")


def desired_dir(values):
    d = tempfile.mkdtemp()
    for name, value in values.items():
        pathlib.Path(d, name).write_text(value)
    pathlib.Path(d, "..data").mkdir()
    return d


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


# The Git ConfigMap holds the STD-CI-10 values and loads cleanly.
import yaml  # noqa: E402  (self-test installs pyyaml)

cm = next(d for d in yaml.safe_load_all((ROOT / "deploy/foundation/runner-slots.yaml").read_text())
          if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "ci-runner-slots")
want_values = {"vm-k3s-node1": "0", "vm-k3s-node2": "0", "vm-k3s-node3": "0", "vm-k3s-node4": "20",
               "vm-k3s-node5": "20", "vm-k3s-node6": "20", "vm-k3s-node7": "20", "vm-k3s-node8": "4"}
check("git values are STD-CI-10", cm["data"], want_values)
check("load git values", s.load_desired(desired_dir(cm["data"])),
      {k: int(v) for k, v in want_values.items()})
check("pinned zero refused", raises(lambda: s.load_desired(desired_dir({"vm-k3s-node2": "1"}))), True)
check("non-integer refused", raises(lambda: s.load_desired(desired_dir({"vm-k3s-node4": "x"}))), True)
check("bad key refused", raises(lambda: s.load_desired(desired_dir({"Node_4": "1"}))), True)
check("empty refused", raises(lambda: s.load_desired(desired_dir({}))), True)

check("hold_cap", [s.hold_cap(x) for x in ("", "  ", "2026 trip:x", "cap=4 2026 soft:x", "cap=x y")],
      [None, None, 0, 4, 0])
check("caps_from_hold", s.caps_from_hold({"vm-k3s-node8": "cap=2 soft", "vm-k3s-node4": "", "vm-k3s-node5": "trip"}),
      {"vm-k3s-node8": 2, "vm-k3s-node5": 0})
check("decide unheld", s.decide(20, None), "20")
check("decide hard hold", s.decide(4, 0), "0")
check("decide soft cap below", s.decide(4, 2), "2")
check("decide soft cap above", s.decide(4, 9), "4")


def node(value, managers):
    fields = {"f:status": {"f:capacity": {"f:" + RES: {}}, "f:allocatable": {"f:" + RES: {}}}}
    return {"metadata": {"name": "n", "managedFields": [
                {"manager": m, "operation": op, "subresource": "status", "fieldsV1": fields} for m, op in managers]},
            "status": {"capacity": {RES: value}, "allocatable": {RES: value}}}


legacy = [("Python-urllib", "Update")]
mine = [(s.FIELD_MANAGER, "Apply")]
check("equal but legacy-owned applies (ownership cutover)", s.needs_apply(node("20", legacy), "20"), True)
check("equal and owned is noop", s.needs_apply(node("20", mine), "20"), False)
check("owned but drifted applies", s.needs_apply(node("0", mine), "20"), True)
check("absent applies", s.needs_apply({"metadata": {}, "status": {}}, "20"), True)
check("apply body touches only the slot", s.apply_body("vm-k3s-node4", "20"),
      {"apiVersion": "v1", "kind": "Node", "metadata": {"name": "vm-k3s-node4"},
       "status": {"capacity": {RES: "20"}, "allocatable": {RES: "20"}}})


# main(): a hold read error writes nothing; a hold lowers only its node.
def run_main(hold_response, nodes):
    calls = []

    def fake(path, method="GET", body=None, content_type="application/json"):
        calls.append((method, path, body, content_type))
        if path == s.HOLD_PATH:
            if isinstance(hold_response, Exception):
                raise hold_response
            return {"data": hold_response}
        name = path.split("/")[4].split("?")[0]
        return nodes[name]

    s.request = fake
    s.DESIRED_DIR = desired_dir({"vm-k3s-node4": "20", "vm-k3s-node8": "4"})
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = s.main()
    finally:
        sys.stdout = out
    return rc, [c for c in calls if c[0] == "PATCH"]


nodes = {"vm-k3s-node4": node("20", mine), "vm-k3s-node8": node("4", mine)}
rc, patches = run_main(urllib.error.HTTPError(s.HOLD_PATH, 403, "Forbidden", {}, None), nodes)
check("hold 403 fails closed", (rc, patches), (1, []))
rc, patches = run_main(urllib.error.URLError("down"), nodes)
check("hold unreachable fails closed", (rc, patches), (1, []))
rc, patches = run_main(urllib.error.HTTPError(s.HOLD_PATH, 404, "Not Found", {}, None), nodes)
check("no hold object, all owned: noop", (rc, patches), (0, []))
rc, patches = run_main({"vm-k3s-node8": "2026 trip:x"}, nodes)
check("node8 hold applies 0 to node8 only", (rc, [(p[1].split("?")[0], p[2]["status"]["capacity"][RES], p[3]) for p in patches]),
      (0, [("/api/v1/nodes/vm-k3s-node8/status", "0", "application/apply-patch+yaml")]))
check("apply is forced under the estate-ci field manager",
      "fieldManager=estate-ci-runner-slots&force=true" in patches[0][1], True)

if failures:
    print(f"{failures} failure(s)")
    sys.exit(1)
print("runner_slots: OK")
