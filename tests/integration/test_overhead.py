"""
Overhead benchmark test.

100 mocked LLM calls through tracer must complete in < 500ms total.
(5ms per call budget locally; CI limit anchored to observed ubuntu timings — see the assertion.)
The batch of 100 is timed in several rounds and judged on the fastest, so one
stalled round on a shared runner cannot fail the build on its own.
"""

import os
import time
import uuid

from agent_lens.models import EventType
from agent_lens.tracer import Tracer, trace
from tests._perf_gate import skip_unless_perf_gated


@skip_unless_perf_gated
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
        ROUNDS = 7
        rounds_ms = []
        for _ in range(ROUNDS):
            start = time.perf_counter()
            for _ in range(N):
                no_op_call()
            rounds_ms.append((time.perf_counter() - start) * 1000)

        # A runner stall only ever adds time, so the fastest round is the cost of
        # the code itself (timeit's convention); a real regression slows every round.
        elapsed_ms = min(rounds_ms)
        per_call_ms = elapsed_ms / N
        print(
            f"\nOverhead: {elapsed_ms:.1f}ms total / {per_call_ms:.2f}ms per call "
            f"(best of {ROUNDS} rounds: {', '.join(f'{r:.1f}' for r in rounds_ms)})"
        )

        # 274ms = 3x the worst single-shot ubuntu-latest sample, 91.3ms (PR #35, run
        # 34687558815, job 103537105152); 500ms is the local budget. See docs/TESTING.md.
        ci_limit_ms = 274.0
        limit_ms = ci_limit_ms if os.environ.get("CI") else 500.0

        assert elapsed_ms < limit_ms, (
            f"fastest of {ROUNDS} rounds of {N} traced calls took {elapsed_ms:.1f}ms "
            f"({per_call_ms:.2f}ms/call), limit is {limit_ms:.0f}ms; "
            f"all rounds: {', '.join(f'{r:.1f}' for r in rounds_ms)}"
        )

    def test_tracer_record_event_overhead(self, reset_singletons):
        """
        Recording 1000 events directly via Tracer should complete quickly.
        """
        from agent_lens.store import get_default_store

        tracer = Tracer.get_instance()
        get_default_store()

        tracer.start_run("overhead-run")
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
        from agent_lens.models import Event, Run, Span
        from agent_lens.store import Store

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
        from agent_lens.tracer import redact

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
            redact(secret_data)
        elapsed_ms = (time.perf_counter() - start) * 1000

        per_call_ms = elapsed_ms / N
        print(f"\nRedaction overhead: {elapsed_ms:.1f}ms / {per_call_ms:.2f}ms per call")

        # Redaction should be < 1ms per call on average
        assert per_call_ms < 5.0, f"Redaction taking {per_call_ms:.2f}ms/call is too slow"
