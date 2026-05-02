"""
Tests for agent_lens.control (ControlPlane)

Covers:
- Pause/resume cycle works
- Step executes exactly one call then re-pauses
- Fork creates new run sharing parent spans
- Inject delivers synthetic result and resumes
"""

import threading
import time
import uuid

import pytest

from agent_lens.control import ControlPlane
from agent_lens.models import Run, RunStatus, Span
from agent_lens.store import Store


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "ctrl_test.db"
    s = Store(path=db_path)
    yield s
    s.close()


@pytest.fixture
def cp(store):
    c = ControlPlane.get_instance()
    c.set_store(store)
    return c


# ----------------------------------------------------------------
# Pause / Resume
# ----------------------------------------------------------------

class TestPauseResume:
    def test_pause_sets_paused_state(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="test", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        assert cp.should_pause(run.id) is True
        assert cp.get_status(run.id) == "paused"

    def test_resume_clears_paused_state(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="test", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        cp.resume(run.id)

        assert cp.should_pause(run.id) is False
        assert cp.get_status(run.id) == "running"

    def test_pause_blocks_thread_until_resumed(self, cp, store):
        """Pausing a run blocks the agent thread until resume() is called."""
        run = Run(id=str(uuid.uuid4()), name="block-test", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        unblocked = threading.Event()

        def agent_thread():
            # This will block because run is paused
            cp.before_llm_call(run.id)
            unblocked.set()

        t = threading.Thread(target=agent_thread)
        t.start()

        # Thread should be blocked
        assert not unblocked.wait(timeout=0.2), "Thread should still be blocked"

        # Resume — thread should unblock
        cp.resume(run.id)
        assert unblocked.wait(timeout=2.0), "Thread should unblock after resume()"
        t.join(timeout=2.0)

    def test_pause_updates_store_status(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="status-test", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        loaded = store.get_run(run.id)
        assert loaded.status == RunStatus.PAUSED

    def test_resume_updates_store_status(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="status-test-2", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        cp.resume(run.id)
        loaded = store.get_run(run.id)
        assert loaded.status == RunStatus.RUNNING

    def test_double_pause_is_idempotent(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="idempotent", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        cp.pause(run.id)  # Should not raise or deadlock
        assert cp.get_status(run.id) == "paused"


# ----------------------------------------------------------------
# Step
# ----------------------------------------------------------------

class TestStep:
    def test_step_allows_exactly_one_call_then_re_pauses(self, cp, store):
        """
        After step(run_id), before_llm_call() should return once, then
        the second call should block (run is re-paused).
        """
        run = Run(id=str(uuid.uuid4()), name="step-test", start_time=time.time())
        store.save_run(run)

        # Start paused
        cp.pause(run.id)

        # Transition to step mode
        cp.step(run.id, num_calls=1)

        # First call should pass through (not block)
        threading.Event()

        def make_call(call_num, expected_blocked):
            started = threading.Event()
            completed = threading.Event()

            def t():
                started.set()
                cp.before_llm_call(run.id)
                completed.set()

            th = threading.Thread(target=t)
            th.start()
            started.wait(timeout=1.0)

            if expected_blocked:
                # Should be blocked
                assert not completed.wait(timeout=0.3), f"Call {call_num} should be blocked"
                return th, completed
            else:
                # Should complete
                assert completed.wait(timeout=1.0), f"Call {call_num} should not be blocked"
                th.join(timeout=1.0)
                return th, completed

        # First call: should pass through
        th1, done1 = make_call(1, expected_blocked=False)

        # Second call: should be blocked (re-paused)
        th2, done2 = make_call(2, expected_blocked=True)

        # Resume to unblock the second call
        cp.resume(run.id)
        done2.wait(timeout=2.0)
        th2.join(timeout=2.0)


# ----------------------------------------------------------------
# Fork
# ----------------------------------------------------------------

class TestFork:
    def test_fork_creates_new_run(self, cp, store):
        """fork() creates a new Run with parent_run_id set."""
        parent = Run(id=str(uuid.uuid4()), name="parent", start_time=time.time())
        store.save_run(parent)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=parent.id,
            name="fork-point",
            type="llm",
            start_time=time.time(),
        )
        store.save_span(span)

        new_run_id = cp.fork(parent.id, span.id, store=store)

        new_run = store.get_run(new_run_id)
        assert new_run is not None
        assert new_run.parent_run_id == parent.id
        assert new_run.fork_span_id == span.id
        assert new_run.status == RunStatus.RUNNING

    def test_fork_stores_edited_messages(self, cp, store):
        """fork() with edited_messages stores them for injection."""
        parent = Run(id=str(uuid.uuid4()), name="parent2", start_time=time.time())
        store.save_run(parent)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=parent.id,
            name="fork-point-2",
            type="llm",
            start_time=time.time(),
        )
        store.save_span(span)

        edited = [{"role": "user", "content": "edited message"}]
        new_run_id = cp.fork(parent.id, span.id, edited_messages=edited, store=store)

        # The injected result should be available
        with cp._lock:
            assert new_run_id in cp._inject_results
            assert cp._inject_results[new_run_id]["messages"] == edited

    def test_fork_nonexistent_run_raises(self, cp, store):
        with pytest.raises(ValueError, match="not found"):
            cp.fork("nonexistent-run-id", "some-span-id", store=store)

    def test_fork_no_store_raises(self):
        cp2 = ControlPlane.__new__(ControlPlane)
        cp2._lock = threading.Lock()
        cp2._pause_events = {}
        cp2._step_mode = set()
        cp2._step_remaining = {}
        cp2._inject_results = {}
        cp2._forks = {}
        cp2._store = None

        with pytest.raises(RuntimeError, match="Store is not set"):
            cp2.fork("some-id", "some-span", store=None)

    def test_forked_run_has_fork_flag(self, cp, store):
        """Forked run is identified as a fork."""
        parent = Run(id=str(uuid.uuid4()), name="parent3", start_time=time.time())
        store.save_run(parent)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=parent.id,
            name="s",
            type="llm",
            start_time=time.time(),
        )
        store.save_span(span)

        new_run_id = cp.fork(parent.id, span.id, store=store)
        new_run = store.get_run(new_run_id)
        assert new_run.is_fork is True


# ----------------------------------------------------------------
# Inject
# ----------------------------------------------------------------

class TestInject:
    def test_inject_delivers_synthetic_result(self, cp, store):
        """inject() stores a result that before_llm_call() returns."""
        run = Run(id=str(uuid.uuid4()), name="inject-test", start_time=time.time())
        store.save_run(run)

        # Pause the run first
        cp.pause(run.id)

        synthetic = {"output": "mocked LLM response"}
        cp.inject(run.id, synthetic)

        # inject() should also resume the run
        # before_llm_call should return the injected result
        result = cp.before_llm_call(run.id)
        assert result == synthetic

    def test_inject_resumes_blocked_thread(self, cp, store):
        """inject() causes a blocked agent thread to unblock."""
        run = Run(id=str(uuid.uuid4()), name="inject-resume", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)

        returned_value = {}
        unblocked = threading.Event()

        def agent():
            val = cp.before_llm_call(run.id)
            returned_value["value"] = val
            unblocked.set()

        t = threading.Thread(target=agent)
        t.start()

        # Thread is blocked
        assert not unblocked.wait(timeout=0.2)

        # Inject synthetic result
        cp.inject(run.id, {"output": "injected!"})

        assert unblocked.wait(timeout=2.0), "Agent thread should unblock after inject()"
        t.join(timeout=2.0)
        assert returned_value.get("value") == {"output": "injected!"}

    def test_inject_result_consumed_once(self, cp, store):
        """Injected result is only returned once (popped from dict)."""
        run = Run(id=str(uuid.uuid4()), name="inject-once", start_time=time.time())
        store.save_run(run)

        cp.inject(run.id, {"data": "once"})

        # First call returns injected result
        r1 = cp.before_llm_call(run.id)
        assert r1 == {"data": "once"}

        # Second call returns None (no more injected result)
        r2 = cp.before_llm_call(run.id)
        assert r2 is None


# ----------------------------------------------------------------
# Cleanup
# ----------------------------------------------------------------

class TestCleanup:
    def test_cleanup_removes_state(self, cp, store):
        run = Run(id=str(uuid.uuid4()), name="cleanup-test", start_time=time.time())
        store.save_run(run)

        cp.pause(run.id)
        cp.inject(run.id, {"data": "test"})

        cp.cleanup(run.id)

        # After cleanup, no pause state
        assert cp.should_pause(run.id) is False
        with cp._lock:
            assert run.id not in cp._inject_results
