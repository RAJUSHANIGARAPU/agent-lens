"""Fail when demo.tape or the demo script changed without a change to demo.gif.

Usage:
    python scripts/check_demo_drift.py --base <ref> --head <ref>

The diff is taken over ``merge-base(base, head)..head``, so commits that landed on the
base branch after the branch point are not counted. The check looks at changed files
only; it does not verify that the GIF matches the script.

A commit in that range carrying a ``Demo-Drift-Override: <reason>`` trailer (parsed by
git, non-empty reason) waives the check.

Exit codes:
    0  nothing to do, demo.gif changed too, or an override was honoured
    1  drift: a guarded file changed and demo.gif did not
    2  cannot determine (shallow clone, unknown ref, no merge-base, git failure)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from enum import IntEnum
from typing import NamedTuple

GUARDED = ("demo.tape", "examples/07_demo_mock.py")
GIF = "demo.gif"
TRAILER = "Demo-Drift-Override"


class Verdict(IntEnum):
    PASS = 0
    DRIFT = 1
    UNKNOWN = 2


class Result(NamedTuple):
    verdict: Verdict
    message: str


def _git(args: list[str], cwd: str | os.PathLike[str] | None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None


def _unknown(detail: str) -> Result:
    return Result(Verdict.UNKNOWN, f"cannot determine: {detail}")


def check(base: str, head: str, cwd: str | os.PathLike[str] | None = None) -> Result:
    shallow = _git(["rev-parse", "--is-shallow-repository"], cwd)
    if shallow is None or shallow.returncode != 0:
        return _unknown("not a git repository or git not found")
    if shallow.stdout.strip() == "true":
        return _unknown("shallow clone; fetch full history (actions/checkout fetch-depth: 0)")

    shas = []
    for ref in (base, head):
        proc = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
        if proc is None or proc.returncode != 0 or not proc.stdout.strip():
            return _unknown(f"unknown ref '{ref}'")
        shas.append(proc.stdout.strip())
    base_sha, head_sha = shas

    mb_proc = _git(["merge-base", base_sha, head_sha], cwd)
    if mb_proc is None or mb_proc.returncode != 0 or not mb_proc.stdout.strip():
        return _unknown(f"no merge-base between '{base}' and '{head}'")
    merge_base = mb_proc.stdout.strip()

    diff = _git(["diff", "--name-only", "-z", "--no-renames", merge_base, head_sha], cwd)
    if diff is None or diff.returncode != 0:
        detail = diff.stderr.strip() if diff is not None else "git not found"
        return _unknown(f"git diff failed: {detail}")
    changed = {p for p in diff.stdout.split("\0") if p}

    hit = [g for g in GUARDED if g in changed]
    if not hit:
        return Result(Verdict.PASS, "demo-drift: no guarded file changed")
    if GIF in changed:
        return Result(Verdict.PASS, f"demo-drift: {GIF} changed with its inputs")

    log = _git(
        ["log", f"--format=%(trailers:key={TRAILER},valueonly)", f"{merge_base}..{head_sha}"],
        cwd,
    )
    if log is None or log.returncode != 0:
        detail = log.stderr.strip() if log is not None else "git not found"
        return _unknown(f"git log failed: {detail}")
    reasons = [ln.strip() for ln in log.stdout.splitlines() if ln.strip()]
    if reasons:
        return Result(Verdict.PASS, f"demo-drift: override: {reasons[0]}")

    return Result(
        Verdict.DRIFT,
        f"demo drift: {', '.join(hit)} changed but {GIF} did not. Re-render and commit "
        f"{GIF}, or add a '{TRAILER}: <reason>' trailer to a commit in this branch.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if demo inputs changed without demo.gif.")
    parser.add_argument("--base", required=True, help="base ref (for example origin/main)")
    parser.add_argument("--head", required=True, help="head ref (for example HEAD)")
    args = parser.parse_args(argv)

    result = check(args.base, args.head)
    stream = sys.stdout if result.verdict == Verdict.PASS else sys.stderr
    print(result.message, file=stream)
    return int(result.verdict)


if __name__ == "__main__":
    sys.exit(main())
