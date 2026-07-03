"""
Tests for agent_lens.compare.compare_runs — the shared structural run comparison
used by the HTTP diff endpoint and the MCP compare tool.
"""

import time
import uuid

import pytest

from agent_lens.compare import compare_runs
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(path=tmp_path / "compare_test.db")
    yield s
    s.close()


def _seed_llm(store, *, name, message, response, expected_output=None):
    run = Run(id=str(uuid.uuid4()), name=name, start_time=time.time(),
              status=RunStatus.COMPLETED, expected_output=expected_output)
    store.save_run(run)
    span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm", start_time=time.time())
    store.save_span(span)
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_START,
                           data={"messages": [{"role": "user", "content": message}]}))
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_END,
                           data={"latency_ms": 1000, "input_tokens": 10, "output_tokens": 50,
                                 "cost_usd": 0.001, "response": {"content": response}}))
    return run


def test_compare_reports_diff_and_improved_verdict(store):
    run_a = _seed_llm(store, name="a", message="Tell me about Python",
                      response="Python is verbose and powerful.")
    run_b = _seed_llm(store, name="b", message="Briefly: Python?",
                      response="concise and readable.", expected_output="concise")

    result = compare_runs(store, run_a.id, run_b.id)

    assert result["messages_diff"][0]["changed"] is True
    assert result["response_diff"]["changed"] is True
    ar = result["assertion_result"]
    assert ar["passed_in_a"] is False
    assert ar["passed_in_b"] is True
    assert ar["verdict"] == "improved"


def test_compare_metrics_delta(store):
    run_a = _seed_llm(store, name="a", message="x", response="y")
    run_b = _seed_llm(store, name="b", message="x", response="y")
    result = compare_runs(store, run_a.id, run_b.id)
    # Identical latency seeds → zero delta, and total_tokens is summed as a fallback.
    assert result["metrics_delta"]["latency_ms"]["delta"] == 0.0
    assert result["metrics_delta"]["total_tokens"]["a"] == 60


def test_compare_no_assertion_when_expected_output_absent(store):
    run_a = _seed_llm(store, name="a", message="x", response="y")
    run_b = _seed_llm(store, name="b", message="x", response="z")
    result = compare_runs(store, run_a.id, run_b.id)
    assert result["assertion_result"] is None


def test_compare_missing_run_a_raises(store):
    run_b = _seed_llm(store, name="b", message="x", response="y")
    with pytest.raises(ValueError, match="nope"):
        compare_runs(store, "nope", run_b.id)


def test_compare_missing_run_b_raises(store):
    run_a = _seed_llm(store, name="a", message="x", response="y")
    with pytest.raises(ValueError):
        compare_runs(store, run_a.id, "also-nope")
