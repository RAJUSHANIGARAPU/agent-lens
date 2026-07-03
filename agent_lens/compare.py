"""
agent_lens.compare
~~~~~~~~~~~~~~~~~~~
Structural comparison of two runs: message diff, response diff, metrics delta,
and an assertion verdict (improved / regressed / both_pass / neither_pass).

Single source of truth for "how two runs differ" — shared by the HTTP diff
endpoint, the MCP compare tool, and any other caller, so they can never drift.
Kept free of web-framework imports.
"""

from __future__ import annotations

from typing import Any

from agent_lens._textutil import extract_response_text


def _first_data(events: list, event_type: str) -> dict:
    """Return the data of the first event of the given type, or an empty dict."""
    for event in events:
        if event.type == event_type:
            return event.data
    return {}


def _delta(a: float | None, b: float | None) -> dict[str, Any]:
    """Return a numeric delta record with absolute and percentage change."""
    if a is None or b is None:
        return {"a": a, "b": b, "delta": None, "pct_change": None}
    delta = b - a
    pct = round((delta / a) * 100, 1) if a != 0 else None
    return {"a": a, "b": b, "delta": round(delta, 4), "pct_change": pct}


def compare_runs(store, run_a_id: str, run_b_id: str) -> dict[str, Any]:
    """Compare two runs structurally.

    Returns message_diff, response_diff, metrics_delta, thinking_blocks and an
    assertion_result (present only when either run has an expected_output). The
    verdict is comparative — it needs the pair, which is why it lives here and
    not in per-run search/export.

    Raises ValueError if either run is missing (run_a is checked first, matching
    the order the HTTP endpoint reports 404s).
    """
    run_a = store.get_run(run_a_id)
    run_b = store.get_run(run_b_id)
    if run_a is None:
        raise ValueError(f"Run {run_a_id!r} not found")
    if run_b is None:
        raise ValueError(f"Run {run_b_id!r} not found")

    events_a = store.get_events(run_a_id)
    events_b = store.get_events(run_b_id)

    start_a = _first_data(events_a, "llm_start")
    start_b = _first_data(events_b, "llm_start")
    end_a = _first_data(events_a, "llm_end")
    end_b = _first_data(events_b, "llm_end")

    msgs_a = start_a.get("messages", [])
    msgs_b = start_b.get("messages", [])
    max_len = max(len(msgs_a), len(msgs_b))
    messages_diff = []
    for i in range(max_len):
        ma = msgs_a[i] if i < len(msgs_a) else None
        mb = msgs_b[i] if i < len(msgs_b) else None
        messages_diff.append({
            "index": i,
            "role": (ma or mb or {}).get("role"),
            "a": ma.get("content") if ma else None,
            "b": mb.get("content") if mb else None,
            "changed": ma != mb,
        })

    metrics_delta = {
        "latency_ms": _delta(end_a.get("latency_ms"), end_b.get("latency_ms")),
        "total_tokens": _delta(
            end_a.get("total_tokens") or (end_a.get("input_tokens", 0) + end_a.get("output_tokens", 0)) or None,
            end_b.get("total_tokens") or (end_b.get("input_tokens", 0) + end_b.get("output_tokens", 0)) or None,
        ),
        "cost_usd": _delta(end_a.get("cost_usd"), end_b.get("cost_usd")),
    }

    resp_a = extract_response_text(end_a.get("response", {}))
    resp_b = extract_response_text(end_b.get("response", {}))

    assertion_result = None
    expected = run_b.expected_output or run_a.expected_output
    if expected:
        passed_a = expected.lower() in resp_a.lower()
        passed_b = expected.lower() in resp_b.lower()
        assertion_result = {
            "expected_output": expected,
            "passed_in_a": passed_a,
            "passed_in_b": passed_b,
            "verdict": "improved" if (not passed_a and passed_b) else
                       "regressed" if (passed_a and not passed_b) else
                       "both_pass" if (passed_a and passed_b) else "neither_pass",
        }

    return {
        "run_a": {"id": run_a.id, "name": run_a.name, "notes": run_a.notes},
        "run_b": {"id": run_b.id, "name": run_b.name, "notes": run_b.notes},
        "messages_diff": messages_diff,
        "response_diff": {
            "a": resp_a[:2000] if resp_a else None,
            "b": resp_b[:2000] if resp_b else None,
            "changed": resp_a != resp_b,
        },
        "metrics_delta": metrics_delta,
        "thinking_blocks": {
            "a": end_a.get("thinking_blocks", []),
            "b": end_b.get("thinking_blocks", []),
        },
        "assertion_result": assertion_result,
    }
