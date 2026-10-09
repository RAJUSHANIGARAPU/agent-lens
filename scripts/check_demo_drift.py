"""Fail when demo.tape or the demo script changed without a new demo.gif.

Usage:
    python scripts/check_demo_drift.py --base <ref> --head <ref>

The changed files are taken from ``git diff --name-only <base>...<head>`` (three-dot,
so changes that landed on the base branch after the branch point are not counted).

A commit message in ``<base>..<head>`` containing a line of the form
``Demo-Gif-Override: <reason>`` (non-empty reason) waives the check.

Exit codes:
    0  nothing to do, demo.gif changed too, or an override was honoured
    1  drift: a watched file changed and demo.gif did not
    2  the check could not run (bad ref, git missing, not a repository)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

WATCHED = ("demo.tape", "examples/07_demo_mock.py")
GIF = "demo.gif"
TRAILER_RE = re.compile(r"(?m)^Demo-Gif-Override:[ \t]*(\S[^\r\n]*?)[ \t]*$")


class GitError(Exception):
    """git failed or could not be started."""


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def _resolve(ref: str) -> str:
    try:
        return _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except GitError as exc:
        raise GitError(f"cannot resolve ref {ref!r}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="base ref or SHA")
    parser.add_argument("--head", required=True, help="head ref or SHA")
    args = parser.parse_args(argv)

    try:
        base = _resolve(args.base)
        head = _resolve(args.head)
        changed = set(_git("diff", "--name-only", f"{base}...{head}").splitlines())
        hits = sorted(p for p in WATCHED if p in changed)
        if not hits or GIF in changed:
            print("demo-drift: ok")
            return 0
        log = _git("log", "-z", "--format=%h%n%B", f"{base}..{head}")
    except GitError as exc:
        print(f"demo-drift: cannot run: {exc}", file=sys.stderr)
        return 2

    for entry in log.split("\0"):
        match = TRAILER_RE.search(entry)
        if match:
            sha = entry.strip().split("\n", 1)[0]
            print(f"demo-drift: override honoured from {sha}: {match.group(1)}")
            return 0

    print(f"demo-drift: {', '.join(hits)} changed but {GIF} was not changed.", file=sys.stderr)
    print(
        f"Re-render {GIF} and commit it, or add a 'Demo-Gif-Override: <reason>' "
        "trailer to a commit in this PR.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
