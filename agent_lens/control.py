"""
agent_lens.control
~~~~~~~~~~~~~~~~~~
The Pause / Resume / Fork / Step / Inject engine.
This is the heart of agent-lens's runtime control capability.

ControlPlane is a singleton. The Tracer calls into it before each LLM call.
If the run is paused, the tracer's thread blocks here until resumed.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from agent_lens.models import Run, RunStatus


class ControlPlane:
    """
    Singleton controller for pausing, resuming, stepping, and forking agent runs.

    Thread-safety contract:
    - _lock protects all internal dictionaries
    - _pause_events: a threading.Event per run_id. Set = paused (agent blocks).
    - _step_mode: tracks whether a run is in step mode (pause after one call).
    - _step_count: counts LLM calls remaining before re-pausing in step mode.
    - _inject_results: one-shot synthetic tool results to inject into a run.
    """

    _instance: ControlPlane | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # threading.Event per run_id. When set, the run is PAUSED (agent waits).
        self._pause_events: dict[str, threading.Event] = {}
        # Step mode: run will re-pause after _step_remaining[run_id] LLM calls
        self._step_mode: set[str] = set()
        self._step_remaining: dict[str, int] = {}
        # Synthetic results to inject: run_id -> tool_result
        self._inject_results: dict[str, Any] = {}
        # Forked run registry: new_run_id -> (parent_run_id, fork_span_id)
        self._forks: dict[str, tuple[str, str]] = {}
        # Store reference (set after tracer is initialized)
        self._store = None

    @classmethod
    def get_instance(cls) -> ControlPlane:
        """Return the module-level ControlPlane singleton."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = ControlPlane()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (useful in tests)."""
        with cls._instance_lock:
            cls._instance = None

    def set_store(self, store: Any) -> None:
        """Inject the Store dependency (avoids circular imports)."""
        self._store = store

    # ------------------------------------------------------------------
    # Tracer interface — called from inside each traced LLM call
    # ------------------------------------------------------------------

    def should_pause(self, run_id: str) -> bool:
        """Return True if the run is currently paused."""
        with self._lock:
            event = self._pause_events.get(run_id)
            return event is not None and event.is_set()

    def before_llm_call(self, run_id: str) -> Any | None:
        """
        Called by the tracer immediately before an LLM call.
        Blocks if the run is paused, returns injected result if any.
        Returns a synthetic tool result if one was injected, else None.
        """
        # Block until resumed (if paused)
        self._wait_for_resume(run_id)

        # Handle step mode: decrement counter, re-pause if exhausted
        with self._lock:
            if run_id in self._step_mode:
                remaining = self._step_remaining.get(run_id, 0)
                if remaining <= 1:
                    # Re-pause after this call
                    self._step_remaining[run_id] = 0
                    self._ensure_pause_event(run_id)
                    self._pause_events[run_id].set()
                    if run_id in self._step_mode:
                        self._step_mode.discard(run_id)
                else:
                    self._step_remaining[run_id] = remaining - 1

            # Check for injected result
            injected = self._inject_results.pop(run_id, None)

        return injected

    def _wait_for_resume(self, run_id: str) -> None:
        """Block the calling thread until the run is resumed (if paused)."""
        while True:
            with self._lock:
                event = self._pause_events.get(run_id)
                if event is None or not event.is_set():
                    return
            # Block outside the lock so other threads can call resume()
            event.wait(timeout=0.1)

    def _ensure_pause_event(self, run_id: str) -> None:
        """Ensure a pause event exists for run_id (must be called under _lock)."""
        if run_id not in self._pause_events:
            self._pause_events[run_id] = threading.Event()

    # ------------------------------------------------------------------
    # External control API
    # ------------------------------------------------------------------

    def pause(self, run_id: str) -> None:
        """
        Pause a run. The agent's thread will block at the next LLM call.
        Idempotent: calling pause on an already-paused run is a no-op.
        """
        with self._lock:
            self._ensure_pause_event(run_id)
            self._pause_events[run_id].set()

        if self._store:
            self._store.update_run_status(run_id, RunStatus.PAUSED)

    def resume(self, run_id: str) -> None:
        """
        Resume a paused run. The blocked agent thread will unblock.
        Idempotent: calling resume on a running run is a no-op.
        """
        with self._lock:
            event = self._pause_events.get(run_id)
            if event is not None:
                event.clear()

        if self._store:
            self._store.update_run_status(run_id, RunStatus.RUNNING)

    def step(self, run_id: str, num_calls: int = 1) -> None:
        """
        Resume for exactly `num_calls` LLM calls, then pause again.
        The agent thread unblocks, makes one call, then blocks again.
        """
        with self._lock:
            self._step_mode.add(run_id)
            self._step_remaining[run_id] = num_calls
            # Clear the pause event so the agent can proceed
            event = self._pause_events.get(run_id)
            if event is not None:
                event.clear()

        if self._store:
            self._store.update_run_status(run_id, RunStatus.RUNNING)

    def fork(
        self,
        run_id: str,
        span_id: str,
        edited_messages: list[dict] | None = None,
        notes: str | None = None,
        expected_output: str | None = None,
        store: Any | None = None,
    ) -> str:
        """
        Fork a run at the given span.

        Creates a new Run record with:
        - parent_run_id = run_id
        - fork_span_id  = span_id
        - status        = RUNNING

        The new run shares all spans up to span_id by reference (no data duplication).
        Spans after span_id will be recorded fresh in the new run.

        If edited_messages is provided, they are injected as the first LLM call
        in the forked run via the inject mechanism.

        Returns the new run_id.
        """
        effective_store = store or self._store
        if effective_store is None:
            raise RuntimeError("Store is not set on ControlPlane. Call set_store() first.")

        parent_run = effective_store.get_run(run_id)
        if parent_run is None:
            raise ValueError(f"Run {run_id!r} not found.")

        new_run_id = str(uuid.uuid4())
        new_run = Run(
            id=new_run_id,
            name=f"{parent_run.name} [fork]",
            start_time=time.time(),
            status=RunStatus.RUNNING,
            metadata={**parent_run.metadata, "forked_from": run_id, "fork_span_id": span_id},
            parent_run_id=run_id,
            fork_span_id=span_id,
            notes=notes,
            expected_output=expected_output,
        )
        effective_store.save_run(new_run)

        with self._lock:
            self._forks[new_run_id] = (run_id, span_id)
            if edited_messages is not None:
                self._inject_results[new_run_id] = {"messages": edited_messages}

        return new_run_id

    def inject(self, run_id: str, tool_result: Any) -> None:
        """
        Inject a synthetic tool result into a run and resume it.

        The next time the tracer's before_llm_call() is invoked for this run_id,
        it will return `tool_result` instead of None, and the tracer will use it
        as the LLM response (bypassing the actual API call).

        The run is also resumed so it can make that injected call.
        """
        with self._lock:
            self._inject_results[run_id] = tool_result
            # Clear the pause event so the agent unblocks
            event = self._pause_events.get(run_id)
            if event is not None:
                event.clear()

    def get_status(self, run_id: str) -> str:
        """Return "paused" or "running" for a given run."""
        with self._lock:
            event = self._pause_events.get(run_id)
            if event is not None and event.is_set():
                return "paused"
            return "running"

    def cleanup(self, run_id: str) -> None:
        """Remove all control state for a completed run."""
        with self._lock:
            self._pause_events.pop(run_id, None)
            self._step_mode.discard(run_id)
            self._step_remaining.pop(run_id, None)
            self._inject_results.pop(run_id, None)
