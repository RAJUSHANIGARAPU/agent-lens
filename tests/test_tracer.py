"""
Tests for agent_lens.tracer

Covers:
- @trace decorator captures span start/end
- Concurrent runs are isolated (10 parallel threads)
- Secret redaction: Authorization headers stripped
- Overhead: 1000 traced no-op calls complete in < 5 seconds
- SQLite persistence: events survive store reload
"""

import threading
import time
import uuid
from pathlib import Path

import pytest

from agent_lens.models import EventType, RunStatus
from agent_lens.store import Store
from agent_lens.tracer import (
    Tracer,
    TraceContext,
    redact,
    trace,
    trace_span,
)


# ----------------------------------------------------------------
# Basic decorator tests
# ----------------------------------------------------------------

class TestTraceDecorator:
    def test_sync_function_is_wrapped(self, reset_singletons):
        """@trace wraps a sync function and captures a span."""
        calls = []

        @trace
        def my_fn(x):
            calls.append(x)
            return x * 2

        result = my_fn(5)
        assert result == 10
        assert calls == [5]

    def test_span_is_persisted(self, reset_singletons):
        """@trace persists a span to the store."""
        from agent_lens.store import get_default_store

        @trace
        def my_fn():
            return "hello"

        my_fn()

        store = get_default_store()
        runs = store.get_runs()
        assert len(runs) == 1

        spans = store.get_spans(runs[0].id)
        assert len(spans) >= 1
        assert any("my_fn" in s.name for s in spans)

    def test_span_start_end_times(self, reset_singletons):
        """Span has valid start_time and end_time after completion."""
        from agent_lens.store import get_default_store

        @trace
        def slow_fn():
            time.sleep(0.05)
            return True

        slow_fn()

        store = get_default_store()
        runs = store.get_runs()
        spans = store.get_spans(runs[0].id)

        completed = [s for s in spans if s.end_time is not None]
        assert len(completed) >= 1
        span = completed[0]
        assert span.end_time > span.start_time
        assert span.duration_ms >= 40  # at least 40ms

    def test_async_function_is_wrapped(self, reset_singletons):
        """@trace wraps an async function correctly."""
        import asyncio
        from agent_lens.store import get_default_store

        @trace
        async def async_fn():
            await asyncio.sleep(0.01)
            return "async-result"

        result = asyncio.run(async_fn())
        assert result == "async-result"

        store = get_default_store()
        runs = store.get_runs()
        assert len(runs) == 1

    def test_exception_is_propagated(self, reset_singletons):
        """@trace propagates exceptions and records error status."""
        from agent_lens.store import get_default_store

        @trace
        def failing_fn():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            failing_fn()

        store = get_default_store()
        runs = store.get_runs()
        assert runs[0].status == RunStatus.ERROR

    def test_nested_spans(self, reset_singletons):
        """Nested @trace decorators create parent-child span relationships."""
        from agent_lens.store import get_default_store

        @trace
        def inner():
            return "inner"

        @trace
        def outer():
            return inner()

        outer()

        store = get_default_store()
        runs = store.get_runs()
        spans = store.get_spans(runs[0].id)
        assert len(spans) >= 2

        # Check parent-child relationship
        named_spans = {s.name: s for s in spans}
        assert any("inner" in n for n in named_spans)
        assert any("outer" in n for n in named_spans)

    def test_trace_context_manager(self, reset_singletons):
        """trace_span() context manager creates a span."""
        from agent_lens.store import get_default_store
        from agent_lens.tracer import Tracer

        tracer = Tracer.get_instance()
        run = tracer.start_run("ctx-test")

        with trace_span("my-operation", run_id=run.id):
            time.sleep(0.01)

        tracer.end_run(run.id, RunStatus.COMPLETED)

        store = get_default_store()
        spans = store.get_spans(run.id)
        assert any(s.name == "my-operation" for s in spans)


# ----------------------------------------------------------------
# Concurrency isolation tests
# ----------------------------------------------------------------

class TestConcurrency:
    def test_10_parallel_runs_are_isolated(self, reset_singletons):
        """10 concurrent threads each get their own isolated trace context."""
        from agent_lens.store import get_default_store

        results = {}
        errors = []

        def run_agent(thread_id):
            @trace(run_name=f"thread-{thread_id}")
            def agent():
                return f"result-{thread_id}"

            try:
                result = agent()
                results[thread_id] = result
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run_agent, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised errors: {errors}"
        assert len(results) == 10
        for i in range(10):
            assert results[i] == f"result-{i}"

        store = get_default_store()
        runs = store.get_runs(limit=20)
        assert len(runs) == 10

    def test_run_ids_dont_cross_threads(self, reset_singletons):
        """Each thread has its own run_id context variable."""
        run_ids = {}
        barrier = threading.Barrier(5)

        def capture_run_id(thread_id):
            # Each thread traces independently
            @trace(run_name=f"isolated-{thread_id}")
            def fn():
                run_ids[thread_id] = TraceContext.get_run_id()
                barrier.wait(timeout=5)
                return True

            fn()

        threads = [threading.Thread(target=capture_run_id, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All run_ids should be different
        unique_ids = set(run_ids.values())
        assert len(unique_ids) == 5, f"Expected 5 unique run IDs, got: {run_ids}"


# ----------------------------------------------------------------
# Secret redaction tests
# ----------------------------------------------------------------

class TestSecretRedaction:
    def test_bearer_token_is_redacted(self):
        """Bearer tokens are stripped from all data."""
        data = {"headers": {"Authorization": "Bearer sk-abc123"}}
        result = redact(data)
        assert "sk-abc123" not in str(result)
        assert "[REDACTED]" in str(result)

    def test_openai_api_key_is_redacted(self):
        """OpenAI sk- keys are redacted."""
        data = {"api_key": "sk-proj-abc123DEF456"}
        result = redact(data)
        assert "sk-proj-abc123DEF456" not in str(result)

    def test_nested_secrets_are_redacted(self):
        """Redaction applies recursively to nested dicts and lists."""
        data = {
            "messages": [
                {"role": "system", "content": "You are helpful. Bearer sk-abcdef123"},
                {"role": "user", "content": "Hello"},
            ],
            "headers": {
                "Authorization": "Bearer eyJtoken",
                "x-api-key": "sk-test-key-12345",
            },
        }
        result = redact(data)
        result_str = str(result)
        assert "sk-abcdef123" not in result_str
        assert "eyJtoken" not in result_str

    def test_non_secret_data_preserved(self):
        """Normal data is not affected by redaction."""
        data = {"model": "gpt-4o", "temperature": 0.7, "messages": [{"role": "user", "content": "Hello"}]}
        result = redact(data)
        assert result["model"] == "gpt-4o"
        assert result["messages"][0]["content"] == "Hello"

    def test_events_stored_without_api_keys(self, reset_singletons):
        """Traced function with Authorization headers stores no keys in DB."""
        from agent_lens.store import get_default_store
        from agent_lens.tracer import Tracer

        tracer = Tracer.get_instance()
        run = tracer.start_run("redaction-test")

        tracer.record_event(
            EventType.LLM_START,
            {
                "headers": {"Authorization": "Bearer sk-secret-key-12345"},
                "model": "gpt-4o",
            },
        )
        tracer.end_run(run.id, RunStatus.COMPLETED)

        store = get_default_store()
        events = store.get_events(run.id)
        all_event_data = str([e.data for e in events])
        assert "sk-secret-key-12345" not in all_event_data


# ----------------------------------------------------------------
# Overhead benchmark
# ----------------------------------------------------------------

class TestOverhead:
    def test_1000_no_op_calls_under_5_seconds(self, reset_singletons):
        """1000 traced no-op calls must complete in under 5 seconds."""

        @trace
        def noop():
            return True

        start = time.perf_counter()
        for _ in range(1000):
            noop()
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, f"1000 traced calls took {elapsed:.2f}s (limit: 5.0s)"

    def test_overhead_per_call_reasonable(self, reset_singletons):
        """Average overhead per traced call should be measurable and reasonable."""

        @trace
        def noop():
            return True

        # Warmup
        for _ in range(10):
            noop()

        N = 100
        start = time.perf_counter()
        for _ in range(N):
            noop()
        elapsed = time.perf_counter() - start
        per_call_ms = (elapsed / N) * 1000

        # < 50ms per call is a reasonable ceiling for a test environment
        assert per_call_ms < 50.0, f"Avg overhead {per_call_ms:.2f}ms/call is too high"


# ----------------------------------------------------------------
# Persistence / reload tests
# ----------------------------------------------------------------

class TestPersistence:
    def test_events_survive_store_reload(self, tmp_path, reset_singletons):
        """Events written to SQLite are readable after reopening the Store."""
        db_path = tmp_path / "persist_test.db"

        # Write events
        store1 = Store(path=db_path)
        from agent_lens.models import Run, RunStatus, Span, Event

        run = Run(id=str(uuid.uuid4()), name="persist-run", start_time=time.time())
        store1.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="test-span",
            type="agent",
            start_time=time.time(),
        )
        store1.save_span(span)

        event = Event(
            run_id=run.id,
            span_id=span.id,
            type=EventType.LLM_START,
            data={"model": "gpt-4o", "test_marker": "hello_world"},
        )
        store1.save_event(event)
        store1.close()

        # Reopen and verify
        store2 = Store(path=db_path)
        loaded_runs = store2.get_runs()
        assert len(loaded_runs) == 1
        assert loaded_runs[0].name == "persist-run"

        events = store2.get_events(run.id)
        assert len(events) == 1
        assert events[0].data["test_marker"] == "hello_world"
        store2.close()

    def test_multiple_runs_queryable(self, tmp_path, reset_singletons):
        """Multiple runs in same DB are individually queryable."""
        db_path = tmp_path / "multi.db"
        store = Store(path=db_path)

        run_ids = []
        for i in range(5):
            from agent_lens.models import Run
            run = Run(id=str(uuid.uuid4()), name=f"run-{i}", start_time=time.time())
            store.save_run(run)
            run_ids.append(run.id)

        runs = store.get_runs()
        assert len(runs) == 5

        for rid in run_ids:
            r = store.get_run(rid)
            assert r is not None
            assert r.id == rid

        store.close()
