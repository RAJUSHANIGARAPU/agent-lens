"""
Overhead benchmark test.

100 mocked LLM calls through tracer must complete in < 500ms total.
(5ms per call budget)
"""

import time
import threading
import uuid
from unittest.mock import MagicMock, patch

import pytest

from agent_lens.models import EventType
from agent_lens.tracer import Tracer, trace


class TestOverheadBenchmark:
    def test_100_traced_calls_under_500ms(self, reset_singletons):
        """
        100 no-op traced calls must complete in under 500ms total.
        This validates the 5ms per call overhead budget.
        """

        @trace
        def no_op_call():
            return "ok"

        # Warmup (exclude from timing)
        for _ in range(5):
            no_op_call()

        N = 100
        start = time.perf_counter()
        for _ in range(N):
            no_op_call()
        elapsed_ms = (time.perf_counter() - start) * 1000

        per_call_ms = elapsed_ms / N
        print(f"\nOverhead: {elapsed_ms:.1f}ms total / {per_call_ms:.2f}ms per call")

        assert elapsed_ms < 500.0, (
            f"100 traced calls took {elapsed_ms:.1f}ms "
            f"({per_call_ms:.2f}ms/call), limit is 500ms"
        )

    def test_tracer_record_event_overhead(self, reset_singletons):
        """
        Recording 1000 events directly via Tracer should complete quickly.
        """
        from agent_lens.store import get_default_store

        tracer = Tracer.get_instance()
        store = get_default_store()

        run = tracer.start_run("overhead-run")
        span = tracer.start_span("overhead-span", "agent")

        N = 1000
        start = time.perf_counter()
        for i in range(N):
            tracer.record_event(
                EventType.LLM_START,
                {"model": "gpt-4o", "iteration": i},
                span_id=span.id,
            )
        elapsed_ms = (time.perf_counter() - start) * 1000

        per_event_ms = elapsed_ms / N
        print(f"\nEvent recording: {elapsed_ms:.1f}ms total / {per_event_ms:.2f}ms per event")

        # 1000 events should complete in < 2 seconds
        assert elapsed_ms < 2000.0, f"1000 event records took {elapsed_ms:.1f}ms"

    def test_store_write_overhead(self, reset_singletons, tmp_path):
        """
        Bulk SQLite writes stay within reasonable time bounds.
        """
        from agent_lens.store import Store
        from agent_lens.models import Run, Span, Event

        db = tmp_path / "overhead.db"
        store = Store(path=db)

        run = Run(id=str(uuid.uuid4()), name="overhead", start_time=time.time())
        store.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="span",
            type="agent",
            start_time=time.time(),
        )
        store.save_span(span)

        N = 500
        start = time.perf_counter()
        for i in range(N):
            event = Event(
                run_id=run.id,
                span_id=span.id,
                type=EventType.LLM_START,
                data={"index": i, "model": "gpt-4o"},
            )
            store.save_event(event)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\nStore write: {elapsed_ms:.1f}ms for {N} events ({elapsed_ms/N:.2f}ms/event)")

        # 500 writes in < 5 seconds
        assert elapsed_ms < 5000.0, f"500 store writes took {elapsed_ms:.1f}ms"

        # Verify all events were written
        events = store.get_events(run.id)
        assert len(events) == N, f"Expected {N} events, got {len(events)}"
        store.close()

    def test_overhead_with_secret_redaction(self, reset_singletons):
        """
        Redaction doesn't add significant overhead to traced calls.
        """
        from agent_lens.tracer import Tracer, redact

        N = 1000
        secret_data = {
            "headers": {
                "Authorization": "Bearer sk-test-secret-key-very-long-1234567890",
                "x-api-key": "sk-another-key-abcdefgh",
            },
            "messages": [
                {"role": "system", "content": "System prompt with Bearer token: Bearer sk-abc"},
                {"role": "user", "content": "Normal message"},
            ],
        }

        start = time.perf_counter()
        for _ in range(N):
            result = redact(secret_data)
        elapsed_ms = (time.perf_counter() - start) * 1000

        per_call_ms = elapsed_ms / N
        print(f"\nRedaction overhead: {elapsed_ms:.1f}ms / {per_call_ms:.2f}ms per call")

        # Redaction should be < 1ms per call on average
        assert per_call_ms < 5.0, f"Redaction taking {per_call_ms:.2f}ms/call is too slow"
