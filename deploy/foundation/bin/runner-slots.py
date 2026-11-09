#!/usr/bin/env python3
"""estate-ci runner-slot advertiser (estate-ci#76, D-67/D-87/D-168, STD-CI-10).

Every estate-ci runner Pod requests one agentfleet.vallery.net/ci-runner-slot, the
per-node runner cap. Extended resources on node status are not durable (a kubelet
restart drops them), so this CronJob re-applies them every minute from a static
desired map: ConfigMap ci-runners/ci-runner-slots, mounted at DESIRED_DIR (one file
per node, content = integer slots).

Holds: ConfigMap ci-runners/ci-runner-slot-hold, written by the node8 hot guard
(bin/hot-guard.py). A node with a non-empty hold value advertises 0; a value that
starts with "cap=N " advertises min(desired, N). If the hold cannot be read (any
error other than 404) the run writes nothing and exits 1, so an API error never
reopens a held node.

Writes: for each node in the desired map, and only when the node's capacity or
allocatable value differs or this field manager does not own it yet, one
server-side apply of status.capacity and status.allocatable for ci-runner-slot on
the nodes/status subresource (fieldManager estate-ci-runner-slots, force). Nothing
else on the node is read for writing or written: no labels, taints,
spec.unschedulable or the legacy arc-validation-slot. vm-k3s-node1..3 (control
plane) are refused anything but 0. stdlib only; never prints the token.
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

RESOURCE = "agentfleet.vallery.net/ci-runner-slot"
FIELD_MANAGER = "estate-ci-runner-slots"
HOLD_PATH = "/api/v1/namespaces/ci-runners/configmaps/ci-runner-slot-hold"
PINNED_ZERO = frozenset({"vm-k3s-node1", "vm-k3s-node2", "vm-k3s-node3"})
DESIRED_DIR = os.environ.get("DESIRED_DIR", "/etc/ci-runner-slots")
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NODE_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
TIMEOUT = 15


def api_base():
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        return "https://kubernetes.default.svc"
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    return "https://" + host + ":" + port


def request(path, method="GET", body=None, content_type="application/json"):
    with open(TOKEN_PATH, encoding="utf-8") as stream:
        token = stream.read().strip()
    req = urllib.request.Request(
        api_base() + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "Content-Type": content_type},
    )
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(req, context=context, timeout=TIMEOUT) as response:
        return json.load(response) if response.length != 0 else {}


def load_desired(directory):
    desired = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue  # kubelet ..data / ..<timestamp> ConfigMap mount entries
        if not NODE_NAME.match(name):
            raise ValueError(f"desired-state key {name!r} is not a node name")
        with open(path, encoding="utf-8") as stream:
            raw = stream.read().strip()
        if not raw.isdigit():
            raise ValueError(f"desired-state value for {name} must be a non-negative integer, got {raw!r}")
        desired[name] = int(raw)
    if not desired:
        raise ValueError(f"desired-state directory {directory} names no nodes")
    for name, slots in desired.items():
        if name in PINNED_ZERO and slots != 0:
            raise ValueError(f"refusing to advertise {slots} ci-runner slots on pinned-zero node {name}")
    return desired


def hold_cap(reason):
    """Cap for one hold value: None if empty, N for a leading "cap=N", else 0 (closed)."""
    text = str(reason).strip()
    if not text:
        return None
    head = text.split()[0]
    if head.startswith("cap=") and head[4:].isdigit():
        return int(head[4:])
    return 0


def caps_from_hold(data):
    caps = {name: hold_cap(reason) for name, reason in (data or {}).items()}
    return {name: cap for name, cap in caps.items() if cap is not None}


def load_holds():
    """{node: cap} for held nodes. 404 = no holds; any other error raises."""
    try:
        return caps_from_hold(request(HOLD_PATH).get("data"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise


def decide(slots, cap):
    """ci-runner-slot string for one node: desired, or min(desired, cap) while held."""
    return str(slots) if cap is None else str(min(slots, cap))


def owns(node):
    """True when this field manager owns both status fields for RESOURCE."""
    key = "f:" + RESOURCE
    for entry in node.get("metadata", {}).get("managedFields") or []:
        if entry.get("manager") != FIELD_MANAGER or entry.get("subresource") != "status":
            continue
        status = (entry.get("fieldsV1") or {}).get("f:status") or {}
        if key in (status.get("f:capacity") or {}) and key in (status.get("f:allocatable") or {}):
            return True
    return False


def needs_apply(node, want):
    status = node.get("status") or {}
    return ((status.get("capacity") or {}).get(RESOURCE) != want
            or (status.get("allocatable") or {}).get(RESOURCE) != want
            or not owns(node))


def apply_body(name, want):
    return {"apiVersion": "v1", "kind": "Node", "metadata": {"name": name},
            "status": {"capacity": {RESOURCE: want}, "allocatable": {RESOURCE: want}}}


def reconcile(name, want):
    node = request("/api/v1/nodes/" + name)
    if not needs_apply(node, want):
        return False
    query = urllib.parse.urlencode({"fieldManager": FIELD_MANAGER, "force": "true"})
    request("/api/v1/nodes/" + name + "/status?" + query, method="PATCH",
            body=apply_body(name, want), content_type="application/apply-patch+yaml")
    return True


def main():
    desired = load_desired(DESIRED_DIR)
    try:
        holds = load_holds()
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"hold read failed type={type(error).__name__}; ci-runner-slot left unchanged", flush=True)
        return 1
    for name in sorted(set(holds) - set(desired)):
        print(f"node={name} hold ignored (node not in ci-runner-slots)", flush=True)
    failures = 0
    for name in sorted(desired):
        want = decide(desired[name], holds.get(name))
        held = f" held-cap={holds[name]}" if name in holds else ""
        try:
            action = "applied" if reconcile(name, want) else "noop"
        except urllib.error.HTTPError as error:
            failures += 1
            print(f"node={name} ci-runner-slot={want}{held} action=error http={error.code}", flush=True)
            continue
        except (urllib.error.URLError, OSError, ValueError) as error:
            failures += 1
            print(f"node={name} ci-runner-slot={want}{held} action=error type={type(error).__name__}", flush=True)
            continue
        print(f"node={name} ci-runner-slot={want}{held} action={action}", flush=True)
    if failures:
        print(f"advertise failed for {failures} node(s)", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as error:
        print(f"desired-state rejected: {error}", file=sys.stderr, flush=True)
        sys.exit(2)
