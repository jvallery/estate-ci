#!/usr/bin/env python3
"""estate-ci: rewrite image digest pins. Input: PINS env, lines "<file> <image> <sha256:digest>".

Rewrites kustomize images entries (newName: <image> ... digest:) and inline <image>@sha256: refs.
Lines with an empty digest are skipped. Writes changed=<list> to $PIN_OUTPUT.
<file> may be a glob (*, ?, [): files that do not reference <image> are skipped, and the
line fails only if none of them does (estate-ci#114: runners.yaml and its rendered sets
pin the toolbox in one PR)."""
import glob, os, re, sys


def rewrite(text, image, digest):
    new = re.sub(re.escape(image) + r"@sha256:[0-9a-f]{64}", f"{image}@{digest}", text)
    # kustomize images: entry -> replace the first digest: line after newName: <image>
    lines = new.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(-\s+)?newName:\s*['\"]?" + re.escape(image) + r"['\"]?\s*$", line)
        if not m:
            continue
        for j in range(i + 1, min(i + 6, len(lines))):
            d = re.match(r"^(\s*)digest:\s*\S+\s*$", lines[j])
            if d:
                lines[j] = f"{d.group(1)}digest: {digest}"
                break
            if re.match(r"^\s*-\s", lines[j]):
                break
    new = "\n".join(lines)
    found = new != text or f"{image}@{digest}" in text or f"digest: {digest}" in text
    return new, found


changed = []
for raw in os.environ["PINS"].splitlines():
    parts = raw.split()
    if not parts:
        continue
    if len(parts) == 2:          # digest empty: image not rebuilt
        continue
    if len(parts) != 3:
        sys.exit(f"bad pins line: {raw!r}")
    pattern, image, digest = parts
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit(f"bad digest for {image}")
    if not re.fullmatch(r"[a-z0-9.\-]+(/[a-z0-9._\-]+)+", image):
        sys.exit(f"bad image {image}")
    is_glob = any(c in pattern for c in "*?[")
    paths = sorted(glob.glob(pattern)) if is_glob else [pattern]
    hits = 0
    for path in paths:
        real = os.path.realpath(path)
        if not real.startswith(os.getcwd() + os.sep) or not os.path.isfile(real):
            if is_glob:
                continue
            sys.exit(f"bad file {path}")
        text = open(real).read()
        new, found = rewrite(text, image, digest)
        if not found:
            if is_glob:
                continue
            sys.exit(f"{image} not found in {path}")
        hits += 1
        if new != text:
            open(real, "w").write(new)
            entry = f"{image}@{digest[:19]}"
            if entry not in changed:
                changed.append(entry)
    if not hits:
        sys.exit(f"{image} not found in {pattern}")
with open(os.environ["PIN_OUTPUT"], "a") as out:
    out.write(f"changed={' '.join(changed)}\n")
print("changed:", changed or "nothing")
