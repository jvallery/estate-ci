#!/usr/bin/env python3
"""estate-ci workflow lint: enforce estate CI rules R1-R3 (ADR-0001, ADR-0009).

    lint-workflows.py [--strict] [--require-forgejo-notes] [PATH ...]

PATH is a repository root (default ".") or a workflow file. For a root it
lints .github/workflows, .forgejo/workflows, .gitea/workflows and any local
composite actions (action.yml / action.yaml under .github/actions).

Errors (exit 1):
  R1-hosted-label     runs-on names a GitHub-hosted or generic label
                      (ubuntu-*, windows-*, macos-*, self-hosted, linux, x64, arm64)
  R1-multi-label      runs-on lists more than one label (one estate set label per job)
  R1-no-runner        a job has neither runs-on nor a reusable-workflow uses:
  R2-uses             uses: is not actions/checkout@<40-hex>,
                      jvallery/estate-ci/...@<40-hex> or a local ./ path
  R2-cache-artifact   actions/cache, upload-artifact or download-artifact
  R2-services         services: or container: (needs a Docker daemon)
  R2-trigger          pull_request_target or workflow_run trigger
  R2-environment      environment: (GitHub Environments)
  R2-id-token         permissions grant id-token: write (or write-all)
  R2-ghcr             ghcr.io referenced outside comments
  R3-forgejo-notes    (with --require-forgejo-notes) a reusable workflow
                      (on: workflow_call) has no "Forgejo" notes header comment
Warnings (exit 0 unless --strict):
  R3-context          an expression reads a context outside ADR-0009 rule 5
  R3-forge-secret     secrets.* other than GITHUB_TOKEN (ADR-0009 rule 8)
  R2-inline-shell     a run: block longer than 15 lines (keep logic in scripts)

Standard library only: it runs on the stock runner image (python3, no pip).
The YAML reader handles the block/flow subset workflow files use; scalars stay
strings (like PyYAML's BaseLoader), so `on:` is never read as a boolean.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- YAML subset


class Map(dict):
    """dict that remembers the source line of each key."""

    def __init__(self):
        super().__init__()
        self.line_of: dict[str, int] = {}


class Seq(list):
    def __init__(self):
        super().__init__()
        self.line_of: list[int] = []


class YamlError(Exception):
    pass


_KEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s"'#\-\[\]{}][^#]*?|-[^\s][^#]*?)\s*:(?:\s+|$)""")


def _strip_comment(s: str) -> str:
    """Drop a trailing # comment that is outside quotes."""
    out, q, i = [], None, 0
    while i < len(s):
        c = s[i]
        if q:
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    out.append(c)
                    i += 1
                elif q == '"' and i > 0 and s[i - 1] == "\\":
                    pass
                else:
                    q = None
        elif c in "\"'" and (i == 0 or s[i - 1] in " \t:[{,-"):
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            break
        out.append(c)
        i += 1
    return "".join(out).rstrip()


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return bytes(s[1:-1], "utf-8").decode("unicode_escape") if "\\" in s else s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _split_flow(body: str) -> list[str]:
    parts, depth, q, cur = [], 0, None, []
    for c in body:
        if q:
            cur.append(c)
            if c == q:
                q = None
            continue
        if c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts]


def _flow(s: str, line: int):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        seq = Seq()
        for p in _split_flow(s[1:-1]):
            seq.append(_flow(p, line))
            seq.line_of.append(line)
        return seq
    if s.startswith("{") and s.endswith("}"):
        m = Map()
        for p in _split_flow(s[1:-1]):
            k, sep, v = p.partition(":")
            if not sep:
                raise YamlError(f"line {line}: bad flow mapping entry {p!r}")
            m[_unquote(k)] = _flow(v, line) if v.strip() else ""
            m.line_of[_unquote(k)] = line
        return m
    if s.startswith("&") or s.startswith("*") or s.startswith("!"):
        raise YamlError(f"line {line}: anchors, aliases and tags are not supported")
    return _unquote(s)


class _Reader:
    def __init__(self, text: str):
        self.raw = text.splitlines()
        self.i = 0
        self.anchors: dict = {}

    def _skip(self):
        while self.i < len(self.raw):
            s = self.raw[self.i]
            if s.strip() == "" or s.lstrip().startswith("#") or s.strip() in ("---", "..."):
                self.i += 1
                continue
            break

    def _peek(self):
        self._skip()
        if self.i >= len(self.raw):
            return None
        s = self.raw[self.i]
        if "\t" in s[: len(s) - len(s.lstrip())]:
            raise YamlError(f"line {self.i + 1}: tab indentation")
        return len(s) - len(s.lstrip(" ")), _strip_comment(s.strip())

    def _block_scalar(self, header: str, parent_indent: int) -> str:
        style = header[0]
        chomp = "+" if "+" in header else "-" if "-" in header else ""
        lines, ind = [], None
        while self.i < len(self.raw):
            s = self.raw[self.i]
            if s.strip() == "":
                lines.append("")
                self.i += 1
                continue
            cur = len(s) - len(s.lstrip(" "))
            if cur <= parent_indent:
                break
            if ind is None:
                ind = cur
            if cur < ind:
                break
            lines.append(s[ind:])
            self.i += 1
        trailing = 0
        while lines and lines[-1] == "":
            lines.pop()
            trailing += 1
        if style == "|":
            body = "\n".join(lines)
        else:
            body, prev, blanks = "", None, 0
            for ln in lines:
                if ln == "":
                    blanks += 1
                    continue
                if prev is None:
                    body = "\n" * blanks + ln
                else:
                    more = prev.startswith(" ") or ln.startswith(" ")
                    body += ("\n" if more else " ") if blanks == 0 else "\n" * (blanks + more)
                    body += ln
                prev, blanks = ln, 0
        if not lines:
            return ""
        if chomp == "-":
            return body
        if chomp == "+":
            return body + "\n" * (trailing + 1)
        return body + "\n"

    def _value(self, rest: str, indent: int, line: int):
        """Value after 'key:' or '- '. rest is comment-stripped."""
        if rest.startswith("*"):
            name = rest[1:].strip()
            if name not in self.anchors:
                raise YamlError(f"line {line}: unknown alias *{name}")
            return self.anchors[name]
        if rest.startswith("&"):
            name, _, rest = rest[1:].partition(" ")
            value = self._value(rest.strip(), indent, line)
            self.anchors[name] = value
            return value
        if rest == "":
            nxt = self._peek()
            if nxt is None:
                return ""
            ni, ns = nxt
            if ni > indent or (ni == indent and (ns == "-" or ns.startswith("- "))):
                return self.node(ni)
            return ""
        if rest[0] in "|>":
            return self._block_scalar(rest, indent)
        if rest[0] in "[{":
            depth = rest.count("[") + rest.count("{") - rest.count("]") - rest.count("}")
            while depth > 0 and self.i < len(self.raw):
                more = _strip_comment(self.raw[self.i].strip())
                self.i += 1
                rest += " " + more
                depth += more.count("[") + more.count("{") - more.count("]") - more.count("}")
            return _flow(rest, line)
        if rest[0] in "\"'" and not (len(rest) > 1 and rest.endswith(rest[0])):
            # multi-line quoted scalar: fold continuation lines
            while self.i < len(self.raw):
                more = self.raw[self.i].strip()
                self.i += 1
                rest += " " + more
                if more.endswith(rest[0]):
                    break
            return _unquote(rest)
        if rest.startswith("!"):
            raise YamlError(f"line {line}: tags are not supported")
        # plain multi-line scalar continuation
        while True:
            nxt = self._peek()
            if nxt is None or nxt[0] <= indent or _KEY.match(nxt[1]) or nxt[1].startswith("- ") or nxt[1] == "-":
                break
            rest += " " + nxt[1]
            self.i += 1
        return _unquote(rest)

    def node(self, indent: int):
        first = self._peek()
        if first is None:
            return ""
        if first[1] == "-" or first[1].startswith("- "):
            return self._seq(indent)
        return self._map(indent)

    def _seq(self, indent: int):
        seq = Seq()
        while True:
            nxt = self._peek()
            if nxt is None:
                return seq
            ci, s = nxt
            if ci != indent or not (s == "-" or s.startswith("- ")):
                if ci > indent:
                    raise YamlError(f"line {self.i + 1}: unexpected indentation")
                return seq
            line = self.i + 1
            raw = self.raw[self.i]
            body = _strip_comment(raw.strip())[1:].lstrip()
            self.i += 1
            seq.line_of.append(line)
            if body == "":
                seq.append(self._value("", indent, line))
            elif _KEY.match(body) and not body.startswith(("[", "{", '"', "'")) or (
                body[0] in "\"'" and _KEY.match(body)
            ):
                # "- key: value" starts a mapping whose keys sit at the body column
                dash = len(raw) - len(raw.lstrip(" "))
                after = raw[dash + 1:]
                col = dash + 1 + len(after) - len(after.lstrip(" "))
                self.i -= 1
                self.raw[self.i] = " " * col + after.lstrip(" ")
                seq.append(self._map(col))
            else:
                seq.append(self._value(body, indent, line))

    def _map(self, indent: int):
        m = Map()
        while True:
            nxt = self._peek()
            if nxt is None:
                return m
            ci, s = nxt
            if ci < indent:
                return m
            if ci > indent:
                raise YamlError(f"line {self.i + 1}: unexpected indentation")
            if s == "-" or s.startswith("- "):
                return m
            km = _KEY.match(s)
            if not km:
                raise YamlError(f"line {self.i + 1}: expected 'key:' in {s!r}")
            key = _unquote(km.group(1))
            line = self.i + 1
            rest = s[km.end():].strip()
            self.i += 1
            m.line_of[key] = line
            m[key] = self._value(rest, indent, line)


def load(text: str):
    r = _Reader(text)
    first = r._peek()
    if first is None:
        return Map()
    doc = r.node(first[0])
    if r._peek() is not None:
        raise YamlError(f"line {r.i + 1}: content after the document")
    return doc


# --------------------------------------------------------------------------- rules

HOSTED = re.compile(
    r"^(ubuntu-[\w.-]+|windows-[\w.-]+|macos-[\w.-]+|self-hosted|linux|x64|arm64|arm|windows|macos)$",
    re.IGNORECASE,
)
SHA = r"[0-9a-f]{40}"
ALLOWED_USES = [
    re.compile(rf"^actions/checkout@{SHA}$"),
    re.compile(rf"^jvallery/estate-ci/[A-Za-z0-9_./-]+@{SHA}$"),
    re.compile(r"^\./[A-Za-z0-9_./-]+$"),
]
CACHE_ARTIFACT = re.compile(r"^actions/(cache|upload-artifact|download-artifact)(/|@)")
ALLOWED_CONTEXTS = {
    "github.repository", "github.ref", "github.ref_name", "github.sha", "github.event_name",
    "github.event.pull_request.number", "github.event.pull_request.head.sha",
    "github.workflow", "github.token",
}
ALLOWED_CONTEXT_ROOTS = ("inputs.", "needs.", "matrix.", "steps.", "env.", "jobs.", "vars.", "runner.")
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
CTX_REF = re.compile(r"(?<![\w.'\"])((?:github|secrets)(?:\.[A-Za-z_][\w-]*|\[[^\]]*\])+)")
INLINE_SHELL_MAX = 15


class Finding:
    def __init__(self, path, line, rule, msg, error=True):
        self.path, self.line, self.rule, self.msg, self.error = path, line, rule, msg, error

    def __str__(self):
        kind = "error" if self.error else "warning"
        return f"{self.path}:{self.line}: {kind} [{self.rule}] {self.msg}"


def _labels(value):
    if isinstance(value, Map):
        out = []
        for k in ("labels", "group"):
            v = value.get(k)
            if isinstance(v, Seq):
                out.extend(str(x) for x in v)
            elif v:
                out.append(str(v))
        return out
    if isinstance(value, Seq):
        return [str(x) for x in value]
    return [str(value)]


def _hosted_in_expr(label: str):
    """Quoted literals inside ${{ }} that name hosted labels."""
    bad = []
    for expr in EXPR.findall(label):
        for lit in re.findall(r"'([^']*)'|\"([^\"]*)\"", expr):
            text = lit[0] or lit[1]
            if HOSTED.match(text):
                bad.append(text)
    return bad


def _check_uses(f, ref: str, line: int, out):
    ref = ref.strip()
    if CACHE_ARTIFACT.match(ref):
        out.append(Finding(f, line, "R2-cache-artifact", f"{ref}: Actions cache/artifact services are not allowed; persist to registry.vallery.net or Garage"))
        return
    if not any(p.match(ref) for p in ALLOWED_USES):
        out.append(Finding(f, line, "R2-uses", f"{ref}: only actions/checkout@<40-hex>, jvallery/estate-ci/...@<40-hex> or ./local paths"))


def _steps(f, steps, out):
    if not isinstance(steps, Seq):
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, Map):
            continue
        if "uses" in step:
            _check_uses(f, str(step["uses"]), step.line_of["uses"], out)
        run = step.get("run")
        if isinstance(run, str) and run.count("\n") + (0 if run.endswith("\n") else 1) > INLINE_SHELL_MAX:
            out.append(Finding(f, step.line_of["run"], "R2-inline-shell",
                               f"run: block has more than {INLINE_SHELL_MAX} lines; move the logic into a repository script", error=False))


def _triggers(on):
    if isinstance(on, Map):
        return list(on.keys())
    if isinstance(on, Seq):
        return [str(x) for x in on]
    return [str(on)] if on else []


def _id_token(perms) -> bool:
    if isinstance(perms, str):
        return perms.strip() == "write-all"
    return isinstance(perms, Map) and str(perms.get("id-token", "")).strip() == "write"


def lint_workflow(f: str, text: str, require_notes: bool):
    out: list[Finding] = []
    try:
        doc = load(text)
    except YamlError as e:
        return [Finding(f, 0, "parse", str(e))]
    if not isinstance(doc, Map):
        return [Finding(f, 1, "parse", "workflow is not a mapping")]

    # ghcr.io anywhere outside comments; secrets and contexts in expressions
    for n, line in enumerate(text.splitlines(), 1):
        code = _strip_comment(line)
        if "ghcr.io" in code:
            out.append(Finding(f, n, "R2-ghcr", "ghcr.io reference: images come from registry.vallery.net (pinned by digest)"))
        for expr in EXPR.findall(code):
            for ref in CTX_REF.findall(expr):
                ref = re.sub(r"\[['\"]?([^\]'\"]*)['\"]?\]", r".\1", ref)
                if ref.startswith("secrets."):
                    if ref != "secrets.GITHUB_TOKEN":
                        out.append(Finding(f, n, "R3-forge-secret", f"{ref}: credentials live in Kubernetes Secrets mounted by the runner profile (ADR-0009 rule 8)", error=False))
                elif ref not in ALLOWED_CONTEXTS:
                    out.append(Finding(f, n, "R3-context", f"{ref}: not a portable context (ADR-0009 rule 5); pass it via a script env var", error=False))

    on_key = "on" if "on" in doc else ("true" if "true" in doc else None)
    triggers = _triggers(doc.get(on_key)) if on_key else []
    for t in triggers:
        if t in ("pull_request_target", "workflow_run"):
            out.append(Finding(f, doc.line_of.get(on_key, 1), "R2-trigger", f"{t} trigger is not allowed (privileged context for untrusted code; not portable)"))
    if require_notes and "workflow_call" in triggers and "Forgejo" not in "\n".join(
        l for l in text.splitlines() if l.lstrip().startswith("#")
    ):
        out.append(Finding(f, 1, "R3-forgejo-notes", "reusable workflow has no 'Forgejo' portability notes header comment"))
    if _id_token(doc.get("permissions")):
        out.append(Finding(f, doc.line_of.get("permissions", 1), "R2-id-token", "id-token: write (OIDC) is GitHub-only"))

    jobs = doc.get("jobs")
    if not isinstance(jobs, Map):
        out.append(Finding(f, doc.line_of.get("jobs", 1), "parse", "no jobs mapping"))
        return out
    for jid, job in jobs.items():
        jl = jobs.line_of[jid]
        if not isinstance(job, Map):
            continue
        for key, rule, msg in (
            ("services", "R2-services", "services: needs a Docker daemon; use a runner profile sidecar (ADR-0004)"),
            ("container", "R2-services", "container: needs a Docker daemon; use the profile's job image (in-job-image)"),
            ("environment", "R2-environment", "GitHub Environments are not portable; credentials come from the runner profile (ADR-0005)"),
        ):
            if key in job:
                out.append(Finding(f, job.line_of[key], rule, f"job {jid}: {msg}"))
        if _id_token(job.get("permissions")):
            out.append(Finding(f, job.line_of["permissions"], "R2-id-token", f"job {jid}: id-token: write (OIDC) is GitHub-only"))
        if "uses" in job:
            _check_uses(f, str(job["uses"]), job.line_of["uses"], out)
            continue
        if "runs-on" not in job:
            out.append(Finding(f, jl, "R1-no-runner", f"job {jid}: no runs-on"))
        else:
            rl = job.line_of["runs-on"]
            labels = _labels(job["runs-on"])
            if len(labels) != 1:
                out.append(Finding(f, rl, "R1-multi-label", f"job {jid}: runs-on must be exactly one estate scale-set label, got {labels}"))
            for label in labels:
                if HOSTED.match(label.strip()):
                    out.append(Finding(f, rl, "R1-hosted-label", f"job {jid}: '{label}' is a GitHub-hosted or generic label; use an ARC set ('<repo>-ci')"))
                for lit in _hosted_in_expr(label):
                    out.append(Finding(f, rl, "R1-hosted-label", f"job {jid}: expression can select hosted label '{lit}'"))
        _steps(f, job.get("steps"), out)
    return out


def lint_action(f: str, text: str):
    out: list[Finding] = []
    try:
        doc = load(text)
    except YamlError as e:
        return [Finding(f, 0, "parse", str(e))]
    runs = doc.get("runs") if isinstance(doc, Map) else None
    if isinstance(runs, Map):
        using = str(runs.get("using", ""))
        if using.startswith("docker") or using.startswith("node"):
            out.append(Finding(f, runs.line_of.get("using", 1), "R2-uses", f"local action using '{using}': only composite actions that call scripts"))
        _steps(f, runs.get("steps"), out)
    for n, line in enumerate(text.splitlines(), 1):
        if "ghcr.io" in _strip_comment(line):
            out.append(Finding(f, n, "R2-ghcr", "ghcr.io reference"))
    return out


def collect(path: Path):
    if path.is_file():
        return [path]
    files = []
    for d in (".github/workflows", ".forgejo/workflows", ".gitea/workflows"):
        p = path / d
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix in (".yml", ".yaml") and x.is_file())
    a = path / ".github/actions"
    if a.is_dir():
        files += sorted(x for x in a.rglob("action.y*ml"))
    return files


def main(argv):
    strict = "--strict" in argv
    notes = "--require-forgejo-notes" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")] or [Path(".")]
    unknown = [a for a in argv if a.startswith("--") and a not in ("--strict", "--require-forgejo-notes")]
    if unknown:
        print(f"unknown option(s): {' '.join(unknown)}", file=sys.stderr)
        return 2
    findings, n = [], 0
    for p in paths:
        for f in collect(p):
            n += 1
            text = f.read_text(encoding="utf-8")
            if f.name.startswith("action.") and f.parent.parent.name == "actions":
                findings += lint_action(str(f), text)
            else:
                findings += lint_workflow(str(f), text, notes)
    for x in findings:
        print(x, file=sys.stderr)
    errors = [x for x in findings if x.error or strict]
    warns = len(findings) - len([x for x in findings if x.error])
    if errors:
        print(f"lint-workflows: FAILED: {len(errors)} error(s), {warns} warning(s) in {n} file(s) (estate rules R1-R3; see estate-ci docs/adr/0009)", file=sys.stderr)
        return 1
    print(f"lint-workflows: {n} file(s) OK ({warns} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
