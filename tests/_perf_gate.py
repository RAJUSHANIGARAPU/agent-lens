"""Where the tracer overhead benchmarks are allowed to skip.

The README quotes per-call overhead numbers measured by these benchmarks on
Linux CI, so a skip there would leave a published claim untested while CI stays
green. The predicate lives here, rather than inline in each ``skipif``, so
``tests/test_perf_gate.py`` can pin down exactly where skipping is allowed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

import pytest


def perf_gate_skipped(platform: str = sys.platform, env: Mapping[str, str] = os.environ) -> bool:
    """True where the perf budget is not enforced: Windows, and non-Linux CI runners."""
    return platform == "win32" or (bool(env.get("CI")) and platform != "linux")


skip_unless_perf_gated = pytest.mark.skipif(
    perf_gate_skipped(),
    reason="Perf budget is gated on Linux CI and on local non-Windows machines; "
    "shared macOS/Windows CI runners are too noisy to gate on",
)
