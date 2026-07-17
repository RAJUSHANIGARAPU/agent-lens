"""
Tests for agent_lens.export — the framework-free builders that turn stored runs
into records for external search/import tools (the ndjson "ctx document" format
and the Codex-format compatibility shim). Previously 0% covered.

Uses a real SQLite Store (same pattern as test_compare) and seeds runs/events
directly, so the builders are exercised end-to-end without any provider or web
framework.
"""

import json
import time
import uuid

import pytest

from agent_lens.export import (
    EXPORT_TOOL_VERSION,
    _first_llm_end_data,
    assertion_passed,
    codex_records,
    ctx_document,
    ctx_lines,
    iso,
    iter_runs,
)
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(path=tmp_path / "export_test.db")
    yield s
    s.close()


def _seed_run(
    store,
    *,
    name="run",
    status=RunStatus.COMPLETED,
    expected_output=None,
    notes=None,
    parent_run_id=None,
    fork_span_id=None,
    metadata=None,
    message="Tell me about Python",
    response="Python is concise and readable.",
    thinking=None,
    input_tokens=10,
    output_tokens=50,
    total_tokens=None,
    cost=0.002,
    tool=None,
    start=1_000.0,
    end=1_002.0,
):
    run = Run(
        id=str(uuid.uuid4()), name=name, start_time=start, end_time=end,
        status=status, expected_output=expected_output, notes=notes,
        parent_run_id=parent_run_id, fork_span_id=fork_span_id, metadata=metadata or {},
    )
    store.save_run(run)
    span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm", start_time=start)
    store.save_span(span)
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_START,
                           timestamp=start, data={"messages": [{"role": "user", "content": message}]}))
    if tool:
        store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.TOOL_START,
                               timestamp=start + 0.4, data={"name": tool, "input": {"q": "x"}}))
        store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.TOOL_END,
                               timestamp=start + 0.5, data={"output": "tool output"}))
    end_data = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                "cost_usd": cost, "response": {"content": response}}
    if total_tokens is not None:
        end_data["total_tokens"] = total_tokens
    if thinking:
        end_data["thinking_blocks"] = thinking
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_END,
                           timestamp=start + 1, data=end_data))
    return run


# ---- iso ---------------------------------------------------------

def test_iso_none_and_epoch():
    assert iso(None) is None
    out = iso(0.0)
    assert out.startswith("1970-01-01T00:00:00")
    assert out.endswith("Z")  # +00:00 normalized to Z


# ---- _first_llm_end_data ----------------------------------------

def test_first_llm_end_data_empty_when_absent():
    assert _first_llm_end_data([]) == {}


# ---- assertion_passed -------------------------------------------

def test_assertion_passed_variants(store):
    hit = _seed_run(store, expected_output="concise", response="Concise and READABLE.")
    miss = _seed_run(store, expected_output="verbose", response="short answer")
    none = _seed_run(store, expected_output=None)

    assert assertion_passed(hit, store.get_events(hit.id)) is True   # case-insensitive substring
    assert assertion_passed(miss, store.get_events(miss.id)) is False
    assert assertion_passed(none, store.get_events(none.id)) is None  # no assertion declared


# ---- ctx_document -----------------------------------------------

def test_ctx_document_shape_and_labels(store):
    run = _seed_run(
        store, name="forked-run", expected_output="concise", notes="testing a shorter prompt",
        parent_run_id="parent-123", fork_span_id="span-9", metadata={"provider": "openai"},
    )
    doc = ctx_document(store, run)

    assert doc["id"] == run.id
    assert doc["source"] == "agent-lens"
    assert doc["title"] == "forked-run"
    assert isinstance(doc["text"], str) and doc["text"]

    m = doc["metadata"]
    assert m["run_id"] == run.id
    assert m["status"] == "completed"
    assert m["is_fork"] is True
    assert m["parent_run_id"] == "parent-123"
    assert m["fork_span_id"] == "span-9"
    assert m["notes"] == "testing a shorter prompt"
    assert m["assertion_passed"] is True
    assert m["total_tokens"] == 60  # fallback: input(10) + output(50)
    assert m["cost_usd"] == 0.002
    assert m["num_events"] == 2  # llm_start + llm_end
    assert m["url"] == f"/runs/{run.id}"
    assert m["started_at"].endswith("Z")
    assert m["duration_ms"] == pytest.approx(2000.0)


def test_ctx_document_prefers_explicit_total_tokens(store):
    run = _seed_run(store, total_tokens=123)
    assert ctx_document(store, run)["metadata"]["total_tokens"] == 123


# ---- codex_records ----------------------------------------------

def test_codex_records_maps_events(store):
    run = _seed_run(
        store, message="hello there", response="hi back",
        thinking=["step one"], tool="web_search", metadata={"provider": "anthropic"},
    )
    records = codex_records(store, run)

    # First record is the session meta header.
    assert records[0]["type"] == "session_meta"
    meta = records[0]["payload"]
    assert meta["id"] == run.id
    assert meta["source"] == "agent-lens"
    assert meta["cli_version"] == EXPORT_TOOL_VERSION
    assert meta["model_provider"] == "anthropic"

    payload_types = [r["payload"].get("type") for r in records]
    assert "message" in payload_types        # user input + assistant output
    assert "reasoning" in payload_types       # thinking block
    assert "function_call" in payload_types   # tool_start
    assert "function_call_output" in payload_types  # tool_end
    assert any(r.get("type") == "event_msg" for r in records)  # task_complete

    # The assistant message content round-trips the response text.
    assistant = [
        r for r in records
        if r["payload"].get("type") == "message" and r["payload"].get("role") == "assistant"
    ]
    assert assistant and assistant[0]["payload"]["content"][0]["text"] == "hi back"


# ---- ctx_lines ---------------------------------------------------

def test_ctx_lines_ndjson_is_single_document(store):
    run = _seed_run(store)
    lines = list(ctx_lines(store, run, "ndjson"))
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["id"] == run.id
    assert doc["source"] == "agent-lens"


def test_ctx_lines_codex_is_multiple_records(store):
    run = _seed_run(store, thinking=["r"], tool="search")
    lines = list(ctx_lines(store, run, "codex"))
    assert len(lines) > 1
    assert all(line.endswith("\n") for line in lines)
    assert json.loads(lines[0])["type"] == "session_meta"


# ---- iter_runs ---------------------------------------------------

def test_iter_runs_filters_by_status(store):
    _seed_run(store, name="ok1", status=RunStatus.COMPLETED, start=time.time())
    _seed_run(store, name="ok2", status=RunStatus.COMPLETED, start=time.time())
    _seed_run(store, name="bad", status=RunStatus.ERROR, start=time.time())

    completed = list(iter_runs(store, status="completed", limit=None))
    assert len(completed) == 2
    assert all(getattr(r.status, "value", r.status) == "completed" for r in completed)


def test_iter_runs_respects_limit(store):
    for i in range(3):
        _seed_run(store, name=f"r{i}", start=time.time())
    assert len(list(iter_runs(store, status=None, limit=1))) == 1
    assert len(list(iter_runs(store, status=None, limit=None))) == 3


def test_codex_records_skips_non_dict_messages(store):
    # A malformed (non-dict) message in the messages list must be skipped, not crash.
    run = Run(id=str(uuid.uuid4()), name="malformed", start_time=1_000.0, end_time=1_001.0,
              status=RunStatus.COMPLETED)
    store.save_run(run)
    span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm", start_time=1_000.0)
    store.save_span(span)
    store.save_event(Event(
        run_id=run.id, span_id=span.id, type=EventType.LLM_START, timestamp=1_000.0,
        data={"messages": ["oops-not-a-dict", {"role": "user", "content": "ok"}]},
    ))
    store.save_event(Event(
        run_id=run.id, span_id=span.id, type=EventType.LLM_END, timestamp=1_001.0,
        data={"response": {"content": "done"}},
    ))

    user_msgs = [r for r in codex_records(store, run) if r["payload"].get("role") == "user"]
    assert len(user_msgs) == 1  # the string message was skipped
    assert user_msgs[0]["payload"]["content"][0]["text"] == "ok"
