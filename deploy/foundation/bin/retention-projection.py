#!/usr/bin/env python3
"""Fail a pull request that changes a retention setting without a capacity projection (estate-ci#263).

STD-OPS-05 and root AGENTS.md L13: any retention change ships with a capacity projection in its
own PR body. This reads the PR's diff and body and, when an added or removed line matches a rule
in retention-keys.txt (next to this script), requires one of:

  * a heading "Capacity projection" (any level) followed by at least one line of content, or
  * the one-line form for reductions, "Capacity projection: reduces retention; no growth"
    (any "Capacity projection: ..." line whose text says it reduces).

    retention-projection.py [--event FILE] [--diff-file FILE] [--body-file FILE] [--keys FILE]

Defaults: --event is $GITHUB_EVENT_PATH (pull_request event; any other event passes), the diff is
`git diff --unified=0 <base> HEAD` in the checkout (base = HEAD^1 of the pull_request merge commit,
else the event's base sha), and the body is the event's pull_request.body. Exit 1 names every
matched file:line. Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HEADING = re.compile(r"^\s{0,3}#{1,6}\s*capacity projection\b.*$", re.I)
ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
ONE_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**capacity projection\**\s*:\s*(\S.*)$", re.I)
COMMENT = re.compile(r"^\s*(#|//|;)")


def load_rules(path):
    match, ignore, skip = [], [], []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            kind, _, rx = line.partition(" ")
            if kind not in ("match", "ignore", "skip-path") or not rx:
                raise SystemExit("retention-projection: bad rule line: %r" % line)
            {"match": match, "ignore": ignore, "skip-path": skip}[kind].append(re.compile(rx.strip(), re.I))
    return match, ignore, skip


def changed_lines(diff):
    """Yield (path, line number, sign, text) for each added/removed line of a unified diff."""
    path, old_no, new_no = None, 0, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            continue
        if line.startswith("--- "):
            source = line[4:].strip()
            if source != "/dev/null":
                path = re.sub(r"^a/", "", source)
            continue
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            old_no, new_no = int(hunk.group(1)), int(hunk.group(2))
            continue
        if line.startswith("+"):
            yield path, new_no, "+", line[1:]
            new_no += 1
        elif line.startswith("-"):
            yield path, old_no, "-", line[1:]
            old_no += 1
        elif line.startswith(" "):
            old_no += 1
            new_no += 1


def matches(diff, rules):
    match, ignore, skip = rules
    found = []
    for path, number, sign, text in changed_lines(diff):
        if path and any(rx.search(path) for rx in skip):
            continue
        if COMMENT.match(text) or any(rx.search(text) for rx in ignore):
            continue
        if any(rx.search(text) for rx in match):
            found.append("%s:%d (%s) %s" % (path, number, "added" if sign == "+" else "removed", text.strip()[:120]))
    return found


def has_projection(body):
    lines = (body or "").replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        one = ONE_LINE.match(line)
        if one and re.search(r"\breduc", one.group(1), re.I):
            return True
        if HEADING.match(line):
            for following in lines[i + 1:]:
                if ANY_HEADING.match(following):
                    break
                if following.strip():
                    return True
    return False


def git_diff(event):
    def run(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True)
    parents = run("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    if len(parents) == 3:  # the pull_request merge commit: HEAD^1 is the base tip
        base = parents[1]
    else:
        base = ((event.get("pull_request") or {}).get("base") or {}).get("sha", "")
        if base and run("cat-file", "-e", base + "^{commit}").returncode:
            run("fetch", "--no-tags", "--depth=1", "origin", base)
    if not base:
        raise SystemExit("retention-projection: cannot find the PR base commit")
    res = run("diff", "--unified=0", "--no-color", base, "HEAD")
    if res.returncode:
        raise SystemExit("retention-projection: git diff failed: " + res.stderr.strip()[:200])
    return res.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--diff-file")
    parser.add_argument("--body-file")
    parser.add_argument("--keys", default=os.path.join(HERE, "retention-keys.txt"))
    args = parser.parse_args(argv)
    event = {}
    if args.event and os.path.exists(args.event):
        with open(args.event, encoding="utf-8") as handle:
            event = json.load(handle)
    if not args.diff_file and "pull_request" not in event:
        print("retention-projection: not a pull_request event; nothing to check")
        return 0
    diff = open(args.diff_file, encoding="utf-8").read() if args.diff_file else git_diff(event)
    body = (open(args.body_file, encoding="utf-8").read() if args.body_file
            else (event.get("pull_request") or {}).get("body") or "")
    found = matches(diff, load_rules(args.keys))
    if not found:
        print("retention-projection: no retention-shaped change in this diff")
        return 0
    if has_projection(body):
        print("retention-projection: %d retention-shaped change(s); the PR body has a capacity projection" % len(found))
        return 0
    print("retention-projection: this PR changes retention-shaped settings but its body has no capacity projection "
          "(STD-OPS-05, AGENTS.md L13):")
    for item in found:
        print("  " + item)
    print("Add a '## Capacity projection' section with the projection (current use, growth, headroom), or for a pure "
          "reduction the line 'Capacity projection: reduces retention; no growth'. Editing the body re-runs this check.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
