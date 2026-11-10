#!/usr/bin/env python3
"""Every image estate-ci delivers comes from the estate registry (estate-ci#114, STD-K8S-07).

Scans the Argo-delivered trees (deploy/foundation, deploy/runners/apps) and
runners.yaml for image references and fails on any that is not a
registry.vallery.net digest. Mirror a new upstream image through mirrors.txt
first, then pin registry.vallery.net/<dest>@<same digest>.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORIGIN = "registry.vallery.net/"
IMAGE = re.compile(r"^\s*(?:-\s+)?(?:[a-z_]*image|[a-zA-Z]*Image):\s*[\"']?([A-Za-z0-9][A-Za-z0-9._/:@-]*[/:@][A-Za-z0-9._/:@-]*)")


def findings(paths):
    out = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = IMAGE.match(line)
            if match and not (match.group(1).startswith(ORIGIN) and "@sha256:" in match.group(1)):
                out.append(f"{path.relative_to(ROOT)}:{number}: {match.group(1)}")
    return out


def main():
    paths = sorted((ROOT / "deploy/foundation").glob("*.yaml")) + sorted((ROOT / "deploy/runners/apps").glob("*.yaml"))
    paths.append(ROOT / "runners.yaml")
    bad = findings(paths)
    for item in bad:
        print(f"not a registry.vallery.net digest: {item}")
    if bad:
        print(f"origin_images: {len(bad)} reference(s); add the image to mirrors.txt and pin it from the origin")
        return 1
    print(f"origin_images: {len(paths)} file(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
