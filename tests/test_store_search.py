"""
Tests for full-text search in agent_lens.store.

Covers the FTS5 path, the LIKE fallback for SQLite builds without FTS5,
reindex idempotency, and correctness across more runs than the get_runs()
default page size.
"""

import time
import uuid

import pytest

from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(path=tmp_path / "search_test.db")
    yield s
    s.close()


def _seed(store, *, name, message="", response="", notes=None, expected_output=None):
    run = Run(id=str(uuid.uuid4()), name=name, start_time=time.time(),
              status=RunStatus.COMPLETED, notes=notes, expected_output=expected_output)
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


class TestSearchRuns:
    def test_search_by_name(self, store):
        run = _seed(store, name="keyword-in-name run")
        hits = store.search_runs("keyword-in-name")
        assert [h["run_id"] for h in hits] == [run.id]

    def test_search_by_notes(self, store):
        run = _seed(store, name="x", notes="the hypothesis was about caching layers")
        hits = store.search_runs("caching")
        assert run.id in [h["run_id"] for h in hits]

    def test_search_by_response_text(self, store):
        run = _seed(store, name="x", response="the migration rolled back cleanly")
        hits = store.search_runs("migration")
        assert run.id in [h["run_id"] for h in hits]

    def test_empty_query_returns_nothing(self, store):
        _seed(store, name="anything")
        assert store.search_runs("") == []
        assert store.search_runs("   ") == []

    def test_reindex_is_idempotent(self, store):
        run = _seed(store, name="dedupe me", message="unique-token-xyz")
        store.reindex_run(run.id)
        store.reindex_run(run.id)
        hits = store.search_runs("unique-token-xyz")
        assert len(hits) == 1

    def test_lazy_index_builds_on_first_search(self, store):
        # Persist directly without reindexing; the first search must build the index.
        run = Run(id=str(uuid.uuid4()), name="lazily indexed alpha", start_time=time.time(),
                  status=RunStatus.COMPLETED)
        store.save_run(run)
        hits = store.search_runs("lazily")
        assert run.id in [h["run_id"] for h in hits]

    def test_fts_hits_are_scored(self, store):
        _seed(store, name="scored run", message="relevance token")
        hits = store.search_runs("relevance")
        assert hits[0]["score"] is not None

    def test_search_beyond_default_page(self, store):
        target = None
        for i in range(150):
            run = _seed(store, name=f"bulk-{i}",
                        message=("needle-in-haystack" if i == 140 else f"filler-{i}"))
            if i == 140:
                target = run
        hits = store.search_runs("needle-in-haystack")
        assert target.id in [h["run_id"] for h in hits]

    def test_terminal_status_triggers_reindex(self, store):
        run = Run(id=str(uuid.uuid4()), name="status run", start_time=time.time(),
                  status=RunStatus.RUNNING)
        store.save_run(run)
        span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm",
                    start_time=time.time())
        store.save_span(span)
        store.reindex_run(run.id)  # index the name only

        # Add response text AFTER indexing — the index is now stale.
        store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_END,
                               data={"response": {"content": "delayedmarkertoken"}}))
        assert store.search_runs("delayedmarkertoken") == []

        # Reaching a terminal status must refresh the index.
        store.update_run_status(run.id, RunStatus.COMPLETED, end_time=time.time())
        assert run.id in [h["run_id"] for h in store.search_runs("delayedmarkertoken")]


class TestLikeFallback:
    @pytest.fixture
    def like_store(self, tmp_path):
        s = Store(path=tmp_path / "fallback.db")
        s._fts_enabled = False  # simulate a SQLite build without FTS5
        yield s
        s.close()

    def test_fallback_search_by_message(self, like_store):
        run = _seed(like_store, name="x", message="fallback searchable phrase")
        hits = like_store.search_runs("searchable")
        assert run.id in [h["run_id"] for h in hits]
        assert hits[0]["score"] is None  # no bm25 in fallback mode

    def test_fallback_status_filter(self, like_store):
        keep = _seed(like_store, name="keep", message="shared beta")
        other = Run(id=str(uuid.uuid4()), name="drop", start_time=time.time(),
                    status=RunStatus.ERROR)
        like_store.save_run(other)
        span = Span(id=str(uuid.uuid4()), run_id=other.id, name="llm", type="llm",
                    start_time=time.time())
        like_store.save_span(span)
        like_store.save_event(Event(run_id=other.id, span_id=span.id, type=EventType.LLM_START,
                                    data={"messages": [{"role": "user", "content": "shared beta"}]}))
        hits = like_store.search_runs("beta", status="completed")
        assert [h["run_id"] for h in hits] == [keep.id]

    def test_fallback_beyond_default_page(self, like_store):
        target = None
        for i in range(150):
            run = _seed(like_store, name=f"bulk-{i}",
                        message=("fallback-needle" if i == 145 else f"filler-{i}"))
            if i == 145:
                target = run
        hits = like_store.search_runs("fallback-needle")
        assert target.id in [h["run_id"] for h in hits]
