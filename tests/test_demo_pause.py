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
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Measured wall time (seconds) of examples/07_demo_mock.py, 2026-10-23, on an
# Apple-silicon Mac (macOS 27.0.1, 11 cores, Python 3.13.0). Only what was
# measured is recorded here:
#   runs of `python examples/07_demo_mock.py >/dev/null` at the start of the
#   session: 46.52 s, 44.85 s, 45.54 s; under pytest 46.66 s.
#   with 11 `yes` busy-loops (one per core): 47.12 s, 60.90 s, 73.67 s, 73.77 s.
#   later pytest runs of this file: demo call 82.68 s, 82.88 s, 84.78 s (the
#   last with `yes` loops), session totals 83.66 s, 83.57 s, 85.99 s; a second
#   reviewer run at 1-min load average ~3.6 on 11 cores: demo call 81.35 s.
# So the same script ran in 45-46 s in some runs and 81-86 s in others, and a
# run at low load average took 81 s. The cause of that difference is
# unexplained; it is not established that load explains it.
# Split (a 46.27 s run): deliberate sleep/pacing vs everything else. The
# script's nominal sleeps add up to ~10 s, but the measured actual sleep time
# was ~43.9 s, so everything else (imports, dashboard start) is ~2.4 s. The
# 400 typed() calls of time.sleep(0.015) took 39.3 s (~98 ms each), so that
# run was pacing-dominated, through sleep overshoot rather than startup or the
# retry loop (urlopen is called once). Coverage is not the cause: the same
# test took 46.66 s with --cov and 50.04 s with --no-cov. The 81-86 s runs
# were not split the same way.
# CI: ci.yml runs `pytest tests/` on ubuntu, macos and windows. In the CI run
# of 2026-10-16 the gap between the preceding test and this one, an upper
# bound on the demo's runtime there, was ~10 s on ubuntu, ~37 s on macos and
# ~13 s on windows.
# If the runtime is really ~46-86 s on this machine, demo.tape's final
# `Sleep 60s` may be too short here; that is the al-017 decision and is not
# changed here.
# Update this when the script's sleeps change.
DEMO_RUNTIME_S = 46.0
# Slowest pasted run above (session total 85.99 s), rounded up to a whole
# second.
DEMO_LOADED_RUNTIME_S = 86.0
# hang guard, not a performance budget: 2x the slowest loaded run so a busy
# machine never trips it, while a real hang is still killed
DEMO_TIMEOUT_S = 2 * DEMO_LOADED_RUNTIME_S
# Stdout line count of the script before the dashboard startup lines were
# silenced; the fix removes exactly 2 of them.
DEMO_STDOUT_LINES = 70
# Short limit for the hang probe; 3 s (not 2 s) so the stub's PID print lands
# before the kill even on a busy machine.
HANG_PROBE_TIMEOUT_S = 3.0


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
    """Run the demo once and share the result between tests (~46-86s on this machine)."""
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


def test_hang_guard_kills_a_hanging_script() -> None:
    stub = "import os, time; print(os.getpid(), flush=True); time.sleep(3600)"
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_script([sys.executable, "-c", stub], timeout=HANG_PROBE_TIMEOUT_S)
    elapsed = time.monotonic() - start
    assert HANG_PROBE_TIMEOUT_S <= elapsed < 10, elapsed

    # On POSIX TimeoutExpired.stdout is bytes even with text=True.
    captured = exc_info.value.stdout or b""
    if isinstance(captured, bytes):
        captured = captured.decode("utf-8")
    assert captured.strip(), "stub printed no PID before the kill; raise HANG_PROBE_TIMEOUT_S"
    pid = int(captured.split()[0])
    if os.name == "posix":
        # subprocess.run kills and reaps the child; signal 0 only probes.
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
