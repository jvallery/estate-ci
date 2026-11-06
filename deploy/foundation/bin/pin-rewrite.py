#!/usr/bin/env python3
"""estate-ci: rewrite image digest pins. Input: PINS env, lines "<file> <image> <sha256:digest>".

Rewrites kustomize images entries (newName: <image> ... digest:) and inline <image>@sha256: refs.
Lines with an empty digest are skipped. Writes changed=<list> to $PIN_OUTPUT."""
import os, re, sys
changed = []
for raw in os.environ["PINS"].splitlines():
    parts = raw.split()
    if not parts:
        continue
    if len(parts) == 2:          # digest empty: image not rebuilt
        continue
    if len(parts) != 3:
        sys.exit(f"bad pins line: {raw!r}")
    path, image, digest = parts
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit(f"bad digest for {image}")
    if not re.fullmatch(r"[a-z0-9.\-]+(/[a-z0-9._\-]+)+", image):
        sys.exit(f"bad image {image}")
    real = os.path.realpath(path)
    if not real.startswith(os.getcwd() + os.sep) or not os.path.isfile(real):
        sys.exit(f"bad file {path}")
    text = open(real).read()
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
    if new == text and f"{image}@{digest}" not in text and f"digest: {digest}" not in text:
        sys.exit(f"{image} not found in {path}")
    if new != text:
        open(real, "w").write(new)
        changed.append(f"{image}@{digest[:19]}")
with open(os.environ["PIN_OUTPUT"], "a") as out:
    out.write(f"changed={' '.join(changed)}\n")
print("changed:", changed or "nothing")
