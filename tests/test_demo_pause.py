"""Regression test for the recorded demo: Run A is paused, resumed, then forked.

Runs examples/07_demo_mock.py as a subprocess and checks the order of the
printed markers. The demo binds the dashboard to port 7878, so that port must
be free; if it is busy the script fails and so does this test. It is
deliberately not skipped or marked xfail.

A second check reads demo.tape and asserts its final Sleep outlasts the script.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Measured wall time (seconds) of examples/07_demo_mock.py. Update this when
# the script's sleeps change.
DEMO_RUNTIME_S = 46.0


def test_demo_pauses_resumes_then_forks_and_gives_verdict() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    # The demo prints box-drawing and arrow characters; a piped stdout on
    # Windows defaults to cp1252 and would raise UnicodeEncodeError.
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "examples/07_demo_mock.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
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
