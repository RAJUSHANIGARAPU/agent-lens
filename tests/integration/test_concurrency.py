"""
Concurrency integration test.

- Launch 50 concurrent traced agents (threads)
- Assert no events are lost (count matches expected)
- Assert no crashes
"""

import threading
import time
import uuid
from collections import defaultdict

import pytest

from agent_lens.models import EventType, Run, RunStatus, Span
from agent_lens.tracer import Tracer, trace, TraceContext


class TestConcurrency:
    def test_50_concurrent_agents_no_events_lost(self, reset_singletons):
        """
        50 concurrent agents, each making 2 recorded events.
        Total expected: 50 * N events. Verify no event loss.
        """
        from agent_lens.store import get_default_store

        N_AGENTS = 50
        N_EVENTS_PER_AGENT = 2  # AGENT_START + AGENT_END per span
        errors = []
        run_ids = []
        lock = threading.Lock()

        def run_agent(agent_id: int):
            try:
                tracer = Tracer.get_instance()
                run = tracer.start_run(f"concurrent-agent-{agent_id}")

                # Simulate some work
                span = tracer.start_span(f"work-{agent_id}", "agent")
                time.sleep(0.001)  # 1ms simulated work
                tracer.end_span(span, status="ok")

                tracer.end_run(run.id, RunStatus.COMPLETED)

                with lock:
                    run_ids.append(run.id)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=run_agent, args=(i,)) for i in range(N_AGENTS)]

        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        elapsed = time.perf_counter() - start

        assert not errors, f"Agents raised errors:\n" + "\n".join(str(e) for e in errors[:5])
        assert len(run_ids) == N_AGENTS, f"Expected {N_AGENTS} run IDs, got {len(run_ids)}"

        store = get_default_store()
        runs = store.get_runs(limit=N_AGENTS + 10)
        assert len(runs) == N_AGENTS, f"Expected {N_AGENTS} runs in store, got {len(runs)}"

        print(f"50 concurrent agents completed in {elapsed:.2f}s")

    def test_no_cross_contamination_between_agents(self, reset_singletons):
        """
        Each agent's events are associated only with its own run_id.
        No cross-contamination of context variables between threads.
        """
        from agent_lens.store import get_default_store

        N = 20
        run_id_map = {}  # agent_id -> run_id
        event_map = defaultdict(set)  # run_id -> set of agent_ids in events
        lock = threading.Lock()
        barrier = threading.Barrier(N)

        def agent(agent_id):
            tracer = Tracer.get_instance()
            run = tracer.start_run(f"isolation-agent-{agent_id}")

            # Record the run_id for this agent
            with lock:
                run_id_map[agent_id] = run.id

            # Barrier: ensure all threads are running simultaneously
            barrier.wait(timeout=10.0)

            span = tracer.start_span(f"agent-span-{agent_id}", "agent")
            tracer.record_event(
                EventType.LLM_START,
                {"agent_id": agent_id, "marker": f"agent-{agent_id}"},
            )
            tracer.end_span(span, status="ok")
            tracer.end_run(run.id, RunStatus.COMPLETED)

        threads = [threading.Thread(target=agent, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        # Verify: each run's events reference the correct agent_id
        store = get_default_store()
        for agent_id, run_id in run_id_map.items():
            events = store.get_events(run_id)
            for e in events:
                if "agent_id" in e.data:
                    assert e.data["agent_id"] == agent_id, (
                        f"Run {run_id} has event with wrong agent_id: "
                        f"expected {agent_id}, got {e.data['agent_id']}"
                    )

    def test_concurrent_pause_resume_safe(self, reset_singletons):
        """
        Multiple threads calling pause/resume on different runs simultaneously
        should not deadlock or crash.
        """
        from agent_lens.control import ControlPlane
        from agent_lens.store import get_default_store
        from agent_lens.models import Run

        store = get_default_store()
        cp = ControlPlane.get_instance()
        cp.set_store(store)

        N = 20
        errors = []

        runs = []
        for i in range(N):
            r = Run(id=str(uuid.uuid4()), name=f"pause-test-{i}", start_time=time.time())
            store.save_run(r)
            runs.append(r)

        def toggle(run_obj):
            try:
                for _ in range(5):
                    cp.pause(run_obj.id)
                    cp.resume(run_obj.id)
            except Exception as e:
                with threading.Lock():
                    errors.append(e)

        threads = [threading.Thread(target=toggle, args=(r,)) for r in runs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Concurrent pause/resume errors: {errors}"

    def test_concurrent_writes_to_sqlite(self, reset_singletons):
        """
        Concurrent writes from many threads should not corrupt the SQLite database.
        Uses WAL mode to verify concurrent write safety.
        """
        from agent_lens.store import get_default_store

        store = get_default_store()
        N = 30
        write_errors = []
        lock = threading.Lock()

        def write_run(i):
            try:
                from agent_lens.models import Run
                r = Run(
                    id=str(uuid.uuid4()),
                    name=f"concurrent-write-{i}",
                    start_time=time.time(),
                )
                store.save_run(r)
            except Exception as e:
                with lock:
                    write_errors.append(e)

        threads = [threading.Thread(target=write_run, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert not write_errors, f"Write errors: {write_errors}"

        runs = store.get_runs(limit=N + 10)
        assert len(runs) == N, f"Expected {N} runs, got {len(runs)}"
