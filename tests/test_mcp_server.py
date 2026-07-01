"""
Tests for agent_lens.mcp_server.

Exercise the tool wrapper functions directly against a temp store — no live
MCP transport is involved, so these run without the optional 'mcp' extra.
"""

import time
import uuid

import pytest

from agent_lens.mcp_server import (
    get_lineage_tool,
    get_run_context_tool,
    search_runs_tool,
)
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(path=tmp_path / "mcp_test.db")
    yield s
    s.close()


def _seed(store, *, name, message="", response="", notes=None, expected_output=None,
          parent_run_id=None):
    run = Run(id=str(uuid.uuid4()), name=name, start_time=time.time(),
              status=RunStatus.COMPLETED, notes=notes, expected_output=expected_output,
              parent_run_id=parent_run_id)
    store.save_run(run)
    span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm", start_time=time.time())
    store.save_span(span)
    if message:
        store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_START,
                               data={"messages": [{"role": "user", "content": message}]}))
    if response:
        store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_END,
                               data={"response": {"content": response}}))
    store.reindex_run(run.id)
    return run


class TestSearchRunsTool:
    def test_finds_and_labels(self, store):
        run = _seed(store, name="mcp searchable",
                    message="something about retry backoff", response="brief",
                    expected_output="brief")
        hits = search_runs_tool("backoff", store=store)
        assert hits[0]["run_id"] == run.id
        assert hits[0]["assertion_passed"] is True
        assert hits[0]["is_fork"] is False

    def test_empty_query_returns_empty(self, store):
        _seed(store, name="x", message="hello")
        assert search_runs_tool("   ", store=store) == []

    def test_limit_is_capped(self, store):
        for i in range(60):
            _seed(store, name=f"run-{i}", message="shared token")
        hits = search_runs_tool("shared", limit=999, store=store)
        assert len(hits) <= 50


class TestGetRunContextTool:
    def test_returns_document(self, store):
        run = _seed(store, name="ctx run", message="the prompt", response="the answer")
        doc = get_run_context_tool(run.id, store=store)
        assert doc["id"] == run.id
        assert "the prompt" in doc["text"]
        assert doc["metadata"]["status"] == "completed"

    def test_missing_run_raises(self, store):
        with pytest.raises(ValueError):
            get_run_context_tool("no-such-run", store=store)


class TestGetLineageTool:
    def test_fork_chain_oldest_first(self, store):
        root = _seed(store, name="root")
        fork = _seed(store, name="fork", notes="hypothesis A", parent_run_id=root.id)
        result = get_lineage_tool(fork.id, store=store)
        assert result["depth"] == 2
        assert [entry["id"] for entry in result["lineage"]] == [root.id, fork.id]
        assert result["lineage"][1]["notes"] == "hypothesis A"

    def test_missing_run_raises(self, store):
        with pytest.raises(ValueError):
            get_lineage_tool("no-such-run", store=store)
