"""Regression test for the recorded demo: Run A is paused, resumed, then forked.

Runs examples/07_demo_mock.py as a subprocess and checks the order of the
printed markers. The demo binds the dashboard to port 7878, so that port must
be free; if it is busy the script fails and so does this test. It is
deliberately not skipped or marked xfail.

A second check reads demo.tape and asserts its final Sleep outlasts the script.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Measured wall time (seconds) of examples/07_demo_mock.py.
# Measured 2026-10-23 on an Apple-silicon Mac (macOS 27.0.1, 11 cores,
# Python 3.13.0), run as `python examples/07_demo_mock.py >/dev/null`:
#   idle:   46.52 s, 44.85 s, 45.54 s (pytest, same machine: 46.66 s)
#   loaded: 47.12 s, 60.90 s, 73.67 s, 73.77 s with 11 `yes` busy-loops
#           (one per core); the spread between loaded runs is large.
#   Later the same day, with other processes already holding the machine
#   (load average ~15, no `yes` of mine), the pytest demo call took 85.89 s
#   and 85.18 s, and 85.08 s with `yes` added on top. So the same script
#   ranges from 45 s to 86 s on one machine depending on background load.
# Split: deliberate sleep/pacing vs everything else. The script's nominal
# sleeps add up to ~10 s, but the measured actual sleep time is ~43.9 s of a
# 46.3 s run, so the rest (imports, dashboard start) is ~2.4 s. The 400
# typed() calls of time.sleep(0.015) took 39.3 s (~98 ms each), so this is
# pacing-dominated, inflated by sleep overshoot on macOS rather than by
# startup or the retry loop (urlopen is called once). Coverage is not the
# cause: the same test took 46.66 s with --cov and 50.04 s with --no-cov.
# CI: ci.yml runs `pytest tests/` on ubuntu, macos and windows. In the CI run
# of 2026-10-16 this test took about 10 s on ubuntu, 37 s on macos and 13 s
# on windows (gap between the preceding test and this one), so the failure at
# 60 s is machine-dependent: it needs a slow-timer or busy machine.
# Update this when the script's sleeps change.
DEMO_RUNTIME_S = 46.0
# Slowest loaded run above (85.89 s), rounded up to a whole second.
DEMO_LOADED_RUNTIME_S = 86.0
# hang guard, not a performance budget: 2x the slowest loaded run so a busy
# machine never trips it, while a real hang is still killed
DEMO_TIMEOUT_S = 2 * DEMO_LOADED_RUNTIME_S
# Stdout line count of the script before the dashboard startup lines were
# silenced; the fix removes exactly 2 of them.
DEMO_STDOUT_LINES = 70


def _run_script(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a script in the repo root with a mandatory kill timeout."""
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    # The demo prints box-drawing and arrow characters; a piped stdout on
    # Windows defaults to cp1252 and would raise UnicodeEncodeError.
    env["PYTHONIOENCODING"] = "utf-8"

    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


@functools.lru_cache(maxsize=1)
def _run_demo() -> subprocess.CompletedProcess[str]:
    """Run the demo once and share the result between tests (it takes ~46s)."""
    return _run_script([sys.executable, "examples/07_demo_mock.py"], timeout=DEMO_TIMEOUT_S)


def test_demo_pauses_resumes_then_forks_and_gives_verdict() -> None:
    proc = _run_demo()
    assert proc.returncode == 0, proc.stdout + proc.stderr

    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    for marker in ("PAUSED", "RESUMED", "fork", '"improved"'):
        assert marker in out, f"missing {marker!r} in demo output:\n{out}"
    assert out.index("PAUSED") < out.index("RESUMED") < out.index("fork") < out.index('"improved"'), out


def test_tape_waits_for_demo_to_finish() -> None:
    tape = (ROOT / "demo.tape").read_text(encoding="utf-8")
    sleeps = [float(m) for m in re.findall(r"(?m)^Sleep (\d+(?:\.\d+)?)s", tape)]
    assert sleeps, "demo.tape has no Sleep line"
    assert 1.25 * DEMO_RUNTIME_S <= sleeps[-1] <= 90, sleeps[-1]


def test_demo_output_has_no_csrf_token() -> None:
    proc = _run_demo()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "CSRF" not in combined
    assert not re.search(r"[0-9a-f]{64}", combined)
    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    assert "RESUMED" in out
    # Guards against redirecting the whole script's stdout.
    assert DEMO_STDOUT_LINES - 4 <= len(proc.stdout.splitlines()) <= DEMO_STDOUT_LINES
