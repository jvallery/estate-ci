#!/usr/bin/env python3
"""Closure check: every Secret a rendered runner set mounts exists live (estate-ci#115).

  kubectl --context vallery -n ci-runners get secret -o name > live.txt
  python3 scripts/check-runner-secrets.py live.txt   # or "-" for stdin

Reads the Secret names that deploy/runners/apps/*.yaml (rendered from runners.yaml)
reference: githubConfigSecret and every volume secretName (zot-push-<repo> for
build sets, zot-pull, ops Secrets). The live list is names only (`secret/<name>`
or bare names), so no Secret data is read. Exits 1 and names each missing Secret
with the sets that mount it. Both sides are visible only to an operator or
verifier with read access to ci-runners: estate-ci self-test cannot read the
cluster, so it runs this against fixtures (tests/runner_secrets/run.py).
"""
import re
import sys
from pathlib import Path

APPS = Path(__file__).resolve().parents[1] / "deploy/runners/apps"
REF = re.compile(r"^\s*(?:secretName|githubConfigSecret):\s*['\"]?([a-z0-9][a-z0-9.-]*)['\"]?\s*$", re.M)


def rendered(apps: Path = APPS) -> dict[str, list[str]]:
    """Secret name -> scale sets that reference it."""
    refs: dict[str, list[str]] = {}
    for f in sorted(apps.glob("*.yaml")):
        if f.name == "kustomization.yaml":
            continue
        for name in REF.findall(f.read_text()):
            refs.setdefault(name, []).append(f.stem)
    return refs


def live_names(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line.rsplit("/", 1)[-1])
    return out


def missing(refs: dict[str, list[str]], live: set[str]) -> dict[str, list[str]]:
    return {k: v for k, v in sorted(refs.items()) if k not in live}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    text = sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text()
    refs = rendered()
    gone = missing(refs, live_names(text))
    for name, sets in gone.items():
        print(f"check-runner-secrets: MISSING ci-runners/{name} (mounted by {', '.join(sets)})")
    if gone:
        return 1
    print(f"check-runner-secrets: OK, {len(refs)} Secret name(s) referenced by rendered sets all exist live")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
