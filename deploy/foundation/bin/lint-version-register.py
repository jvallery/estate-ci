#!/usr/bin/env python3
"""estate-ci component version register check (STD-OPS-02, estate-ci#121).

    lint-version-register.py [--require] [--strict] [PATH ...]

PATH is a repository root (default ".") or a register file. A root's register is
`version-register.json` at the root; a root without one is skipped unless
--require is given. The schema is version-register.schema.json, read from next
to this script (the $ESTATE_CI ConfigMap) or from ../schemas/ in an estate-ci
checkout.

Errors (exit 1):
  VR-missing          (with --require) the root has no version-register.json
  VR-invalid-json     the register does not parse as JSON
  VR-schema           the register breaks the schema (missing or unknown
                      field, wrong type, bad enum value, bad pattern)
  VR-date-invalid     a date field is not a real calendar date
  VR-duplicate        two rows name the same component
  VR-patched-digest   a patched build has no installed.digest (a fork image
                      runs by digest, so the row must name it)
  VR-ref-missing      a ref row names a file outside the repository, a file
                      that does not exist, or a key that file does not hold
Warnings (exit 0 unless --strict):
  VR-eol-past         support.until is before today

A ref row points at another register in the same repository (for example
proxmox-infrastructure `inventory/proxmox-fleet-baseline.yml` version_targets)
and carries no installed or upstream values, so the values exist once.

The reusable lint (lint-workflows.py, lint.yml) runs this for every linted root
that has a version-register.json, so a caller opts in by adding the file; no
workflow pin bump is needed.

Standard library only. It interprets the JSON Schema subset the schema uses:
$ref (local), type, required, properties, additionalProperties: false, enum,
pattern, minLength, minItems, items, anyOf and format: date.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

REGISTER_FILE = "version-register.json"
SCHEMA_FILE = "version-register.schema.json"
TYPES = {"object": dict, "array": list, "string": str}


class Finding:
    def __init__(self, path, rule, msg, error=True):
        self.path, self.rule, self.msg, self.error = path, rule, msg, error

    def __str__(self):
        level = "error" if self.error else "warning"
        return f"{self.path}: {level} [{self.rule}] {self.msg}"


def load_schema():
    here = Path(__file__).resolve().parent
    for p in (here / SCHEMA_FILE, here.parent / "schemas" / SCHEMA_FILE, Path(__file__).parent / SCHEMA_FILE):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"{SCHEMA_FILE} not found next to {__file__} or in ../schemas/")


def resolve(schema, root_schema):
    while "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        node = root_schema
        for part in ref[2:].split("/"):
            node = node[part]
        schema = node
    return schema


def validate(value, schema, root_schema, where):
    """Return a list of (where, message) for value against schema."""
    schema = resolve(schema, root_schema)
    errs = []
    if "anyOf" in schema:
        branches = [validate(value, b, root_schema, where) for b in schema["anyOf"]]
        if all(branches):
            plain = [resolve(b, root_schema) for b in schema["anyOf"]]
            if all(set(b) == {"required"} for b in plain):
                errs.append((where, "needs one of: " + ", ".join(f"'{k}'" for b in plain for k in b["required"])))
            else:
                best = min(range(len(branches)), key=lambda i: len(branches[i]))
                label = plain[best].get("description", "").split(":")[0] or f"branch {best + 1}"
                errs += [(w, f"{m} (as {label})") for w, m in branches[best]]
    if "enum" in schema and value not in schema["enum"]:
        errs.append((where, f"{value!r} is not one of {schema['enum']}"))
        return errs
    want = schema.get("type")
    if want and not isinstance(value, TYPES[want]):
        errs.append((where, f"want {want}, got {type(value).__name__}"))
        return errs
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append((where, "must not be empty"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append((where, f"{value!r} does not match {schema['pattern']}"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append((where, f"missing required field '{key}'"))
        props = schema.get("properties", {})
        for key, sub in value.items():
            if key in props:
                errs += validate(sub, props[key], root_schema, f"{where}.{key}")
            elif schema.get("additionalProperties") is False:
                errs.append((where, f"unknown field '{key}'"))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append((where, f"needs at least {schema['minItems']} item(s)"))
        if "items" in schema:
            for i, item in enumerate(value):
                errs += validate(item, schema["items"], root_schema, f"{where}[{i}]")
    return errs


def dates(value, where):
    """Yield (where, value) for every date-named field (until, read_at, checked)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("until", "read_at", "checked") and isinstance(v, str):
                yield f"{where}.{k}", v
            else:
                yield from dates(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from dates(v, f"{where}[{i}]")


def has_key(path: Path, key: str) -> bool:
    """True when path holds the dotted key: JSON by lookup, anything else by an indented YAML line scan."""
    parts = key.split(".")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            node = json.loads(text)
        except ValueError:
            return False
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True
    lines = text.splitlines()
    start, parent = 0, -1  # the first part must be a top-level key; each next one nested under the last
    for part in parts:
        pat = re.compile(r"^\s*[\"']?" + re.escape(part) + r"[\"']?\s*:(\s|$)")
        for i in range(start, len(lines)):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cur = len(line) - len(line.lstrip())
            if parent >= 0 and cur <= parent:
                return False  # left the parent block
            if (cur == 0 if parent < 0 else True) and pat.match(line):
                start, parent = i + 1, cur
                break
        else:
            return False
    return True


def lint_register(path: Path, root: Path, schema, today: datetime.date):
    shown = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        return [Finding(shown, "VR-invalid-json", str(e))]
    findings = [Finding(shown, "VR-schema", f"{w}: {m}") for w, m in validate(data, schema, schema, "$")]
    for where, v in dates(data, "$"):
        try:
            d = datetime.date.fromisoformat(v)
        except ValueError:
            findings.append(Finding(shown, "VR-date-invalid", f"{where}: {v!r} is not a YYYY-MM-DD date"))
            continue
        if where.endswith(".support.until") and d < today:
            findings.append(Finding(shown, "VR-eol-past", f"{where}: support ended {v}", error=False))
    rows = data.get("components") if isinstance(data, dict) else None
    seen = {}
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        where = f"$.components[{i}]"
        name = row.get("component")
        if isinstance(name, str):
            if name in seen:
                findings.append(Finding(shown, "VR-duplicate", f"{where}: component {name!r} is already row {seen[name]}"))
            seen.setdefault(name, i)
        if "patched" in row and not (isinstance(row.get("installed"), dict) and row["installed"].get("digest")):
            findings.append(Finding(shown, "VR-patched-digest", f"{where}: patched build {name!r} has no installed.digest"))
        ref = row.get("ref")
        if isinstance(ref, dict) and isinstance(ref.get("file"), str) and isinstance(ref.get("key"), str):
            target = (root / ref["file"]).resolve()
            try:
                target.relative_to(root.resolve())
                ok = target.is_file()
            except ValueError:
                ok = False
            if not ok:
                findings.append(Finding(shown, "VR-ref-missing", f"{where}: ref file {ref['file']!r} is not a file in this repository"))
            elif not has_key(target, ref["key"]):
                findings.append(Finding(shown, "VR-ref-missing", f"{where}: {ref['file']} has no key {ref['key']!r}"))
    return findings


def lint_path(p: Path, require: bool, schema, today: datetime.date):
    """Findings for one PATH and whether a register was checked."""
    if p.is_dir():
        f = p / REGISTER_FILE
        if not f.is_file():
            return ([Finding(str(f), "VR-missing", f"no {REGISTER_FILE} (STD-OPS-02)")] if require else []), False
        return lint_register(f, p, schema, today), True
    return lint_register(p, p.parent, schema, today), True


def main(argv, today: datetime.date | None = None):
    require = "--require" in argv
    strict = "--strict" in argv
    unknown = [a for a in argv if a.startswith("--") and a not in ("--require", "--strict")]
    if unknown:
        print(f"unknown option(s): {' '.join(unknown)}", file=sys.stderr)
        return 2
    today = today or datetime.date.today()
    schema = load_schema()
    findings, n = [], 0
    for p in [Path(a) for a in argv if not a.startswith("--")] or [Path(".")]:
        got, checked = lint_path(p, require, schema, today)
        findings += got
        n += checked
    for x in findings:
        print(x, file=sys.stderr)
    errors = [x for x in findings if x.error or strict]
    if errors:
        print(f"lint-version-register: FAILED: {len(errors)} error(s) in {n} register(s) (STD-OPS-02; estate-ci#121)", file=sys.stderr)
        return 1
    print(f"lint-version-register: {n} register(s) OK ({len(findings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
