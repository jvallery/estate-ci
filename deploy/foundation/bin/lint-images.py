#!/usr/bin/env python3
"""estate-ci image lint, rule R4 (storage-infra ADR-016, jvallery/agents#5767 G6).

    lint-images.py [PATH ...]

PATH is a repository root (default "."). Every *.yaml / *.yml file outside
.git, .github, vendored and dependency trees is read line by line.

Errors (exit 1):
  R4-pull-always     a container with an external image (anything not on
                     registry.vallery.net) sets imagePullPolicy: Always, or a
                     Helm values block with an external `repository:` sets
                     pullPolicy: Always. Always forces a registry round trip on
                     every start, so the Pod cannot restart with no WAN even
                     when the node holds the bytes. Use IfNotPresent with a
                     digest-pinned reference.
  R4-allow-expired   an allowlist row is past its expiry date.
  R4-allow-invalid   an allowlist row is malformed.
Warnings:
  R4-tag-only        (with --tags) an external image has no @sha256 digest.

Allowlist: `.estate-ci/image-lint-allow.txt` in the repository, one row per
exception, `<path glob> <image prefix> <YYYY-MM-DD expiry> <issue URL>`;
`#` starts a comment. A row covers a finding when the file path matches the
glob and the image starts with the prefix. Expired rows fail the lint.

Templated images (`{{ ... }}`, `${...}`) are skipped: the value is unknown here.
Standard library only.
"""
from __future__ import annotations

import datetime
import fnmatch
import re
import sys
from pathlib import Path

LOCAL_REGISTRY = "registry.vallery.net/"
SKIP_DIRS = {"fixtures", ".git", ".github", ".forgejo", ".gitea", "node_modules", "vendor", "third_party", ".venv", "venv", "__pycache__"}
MAX_BYTES = 4 * 1024 * 1024
ALLOW_FILE = ".estate-ci/image-lint-allow.txt"

KEY = re.compile(r"^(\s*)(-\s+)?([A-Za-z_][\w.-]*)\s*:\s*(.*?)\s*$")


class Finding:
    def __init__(self, path, line, rule, msg, error=True):
        self.path, self.line, self.rule, self.msg, self.error = path, line, rule, msg, error

    def __str__(self):
        level = "error" if self.error else "warning"
        return f"{self.path}:{self.line}: {level} {self.rule}: {self.msg}"


def scalar(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def parse(lines):
    """(key indent, item start?, key, value) per line, or None for blank/comment/other."""
    out = []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            out.append(None)
            continue
        m = KEY.match(raw)
        if not m:
            indent = len(raw) - len(raw.lstrip())
            out.append((indent, False, None, None))
            continue
        spaces, dash, key, value = m.groups()
        indent = len(spaces) + (len(dash) if dash else 0)
        out.append((indent, bool(dash), key, value))
    return out


def siblings(parsed, i):
    """Keys of the mapping that holds line i (same indent, same list item)."""
    indent, _, _, _ = parsed[i]
    found = {}
    j = i
    while j >= 0:  # backward, including the item-start line
        row = parsed[j]
        if row is not None:
            if row[0] < indent:
                break
            if row[0] == indent and row[2]:
                found.setdefault(row[2], (j, row[3]))
            if row[0] == indent and row[1]:
                break
        j -= 1
    j = i + 1
    while j < len(parsed):  # forward, until the next item or a dedent
        row = parsed[j]
        if row is not None:
            if row[0] < indent or (row[0] == indent and row[1]):
                break
            if row[0] == indent and row[2]:
                found.setdefault(row[2], (j, row[3]))
        j += 1
    return found


def external(image: str) -> bool:
    return bool(image) and not image.startswith(LOCAL_REGISTRY)


def templated(image: str) -> bool:
    return "{{" in image or "${" in image or image.startswith("$")


def lint_text(path: str, text: str, tags: bool):
    lines = text.splitlines()
    parsed = parse(lines)
    findings = []
    for i, row in enumerate(parsed):
        if row is None or row[2] not in ("imagePullPolicy", "pullPolicy", "image"):
            continue
        key, value = row[2], scalar(row[3] or "")
        if key == "image":
            if tags and value and not templated(value) and external(value) and "@sha256:" not in value and "/" in value:
                findings.append(Finding(path, i + 1, "R4-tag-only", f"{value} has no @sha256 digest", error=False))
            continue
        if value != "Always":
            continue
        sib = siblings(parsed, i)
        ref = sib.get("image") if key == "imagePullPolicy" else sib.get("repository")
        if not ref:
            continue
        image = scalar(ref[1] or "")
        if not image or templated(image) or not external(image):
            continue
        findings.append(Finding(path, i + 1, "R4-pull-always",
                                f"{key}: Always on external image {image}; use IfNotPresent with @sha256 (ADR-016)"))
    return findings


def load_allow(root: Path, today: datetime.date):
    rows, findings = [], []
    f = root / ALLOW_FILE
    if not f.is_file():
        return rows, findings
    for n, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        try:
            glob, prefix, expiry, issue = parts
            date = datetime.date.fromisoformat(expiry)
            if not issue.startswith("https://github.com/"):
                raise ValueError("issue")
        except ValueError:
            findings.append(Finding(ALLOW_FILE, n, "R4-allow-invalid", "want '<path glob> <image prefix> <YYYY-MM-DD> <issue URL>'"))
            continue
        if date < today:
            findings.append(Finding(ALLOW_FILE, n, "R4-allow-expired", f"expired {expiry} ({issue})"))
            continue
        rows.append((glob, prefix))
    return rows, findings


def allowed(rows, finding):
    image = finding.msg.split("external image ", 1)[-1].split(";", 1)[0]
    return any(fnmatch.fnmatch(finding.path, g) and image.startswith(p) for g, p in rows)


def collect(root: Path):
    files = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.is_symlink():
                    stack.append(e)
            elif e.suffix in (".yaml", ".yml") and e.is_file():
                try:
                    if e.stat().st_size <= MAX_BYTES:
                        files.append(e)
                except OSError:
                    pass
    return files


def lint_repo(root: Path, tags: bool = False, today: datetime.date | None = None):
    today = today or datetime.date.today()
    rows, findings = load_allow(root, today)
    n = 0
    for f in collect(root):
        n += 1
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(f.relative_to(root))
        for x in lint_text(rel, text, tags):
            if x.rule == "R4-pull-always" and allowed(rows, x):
                continue
            findings.append(x)
    return findings, n


def main(argv):
    tags = "--tags" in argv
    roots = [Path(a) for a in argv if not a.startswith("--")] or [Path(".")]
    findings, n = [], 0
    for root in roots:
        got, count = lint_repo(root, tags)
        findings += got
        n += count
    for x in findings:
        print(x, file=sys.stderr)
    errors = [x for x in findings if x.error]
    if errors:
        print(f"lint-images: FAILED: {len(errors)} error(s) in {n} YAML file(s) (estate rule R4; ADR-016)", file=sys.stderr)
        return 1
    print(f"lint-images: {n} YAML file(s) OK ({len(findings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
