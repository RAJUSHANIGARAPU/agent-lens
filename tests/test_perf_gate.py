"""The overhead benchmarks behind the README's per-call numbers must run on Linux CI."""

from __future__ import annotations

import pytest

from tests._perf_gate import perf_gate_skipped


@pytest.mark.parametrize(
    ("platform", "env", "skipped"),
    [
        ("linux", {"CI": "true"}, False),
        ("linux", {}, False),
        ("darwin", {}, False),
        ("darwin", {"CI": "true"}, True),
        ("win32", {"CI": "true"}, True),
        ("win32", {}, True),
    ],
)
def test_perf_gate_skips_only_where_documented(platform, env, skipped):
    assert perf_gate_skipped(platform, env) is skipped
