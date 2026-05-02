"""
agent_lens.tracer
~~~~~~~~~~~~~~~~~
Core capture engine for agent-lens.

Provides:
- @trace decorator that wraps any function as a Span
- TraceContext: manages the current run/span stack via contextvars
- EventBus: in-process bus that notifies SSE subscribers
- Secret redaction: strips API keys and tokens from all data before storage
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Generator

from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import get_default_store

# ------------------------------------------------------------------
# Secret redaction
# ------------------------------------------------------------------

_REDACT_PATTERNS = [
    # Bearer tokens
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    # OpenAI-style keys (sk-... and sk-proj-...)
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    # Google / Firebase keys
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    # Generic Authorization header value
    re.compile(r"(?i)(authorization|x-api-key|api[-_]?key)\s*[:=]\s*\S+"),
    # Anthropic-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
]

_REPLACEMENT = "[REDACTED]"


def _redact_string(value: str) -> str:
    """Apply all redaction patterns to a string."""
    for pattern in _REDACT_PATTERNS:
        value = pattern.sub(_REPLACEMENT, value)
    return value


def _redact_value(value: Any) -> Any:
    """Recursively redact secrets from a value (dict, list, or str)."""
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Public redaction function used by integrations."""
    return _redact_value(data)  # type: ignore[return-value]


# ------------------------------------------------------------------
# EventBus: bridges sync tracer → async SSE server
# ------------------------------------------------------------------

class EventBus:
    """
    In-process event bus for streaming events to SSE subscribers.

    Thread-safe. The tracer puts events here; the FastAPI SSE handler reads them.
    """

    _instance: "EventBus | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._sub_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = EventBus()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the asyncio event loop for thread-safe dispatch."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber, returns their queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_dict: dict[str, Any]) -> None:
        """Publish an event to all subscribers (thread-safe)."""
        with self._sub_lock:
            subs = list(self._subscribers)

        if not subs:
            return

        loop = self._loop
        if loop is None or not loop.is_running():
            return

        for q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event_dict)
            except Exception:
                pass  # Drop if queue is full; never crash the tracer


# ------------------------------------------------------------------
# TraceContext: per-thread/coroutine trace state
# ------------------------------------------------------------------

_current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("current_span_id", default=None)
_span_stack: ContextVar[list[str]] = ContextVar("span_stack", default=[])


class TraceContext:
    """
    Manages the current run/span state for the active thread/coroutine.
    Uses contextvars for proper async/thread isolation.
    """

    @staticmethod
    def get_run_id() -> str | None:
        return _current_run_id.get()

    @staticmethod
    def get_span_id() -> str | None:
        return _current_span_id.get()

    @staticmethod
    def push_span(span_id: str) -> None:
        stack = list(_span_stack.get())
        stack.append(span_id)
        _span_stack.set(stack)
        _current_span_id.set(span_id)

    @staticmethod
    def pop_span() -> str | None:
        stack = list(_span_stack.get())
        if not stack:
            return None
        popped = stack.pop()
        _span_stack.set(stack)
        _current_span_id.set(stack[-1] if stack else None)
        return popped

    @staticmethod
    def set_run_id(run_id: str) -> None:
        _current_run_id.set(run_id)

    @staticmethod
    def clear() -> None:
        _current_run_id.set(None)
        _current_span_id.set(None)
        _span_stack.set([])


# ------------------------------------------------------------------
# Tracer: the main instrumentation object
# ------------------------------------------------------------------

class Tracer:
    """
    Central tracer singleton. Manages all active runs and spans.
    """

    _instance: "Tracer | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._store = get_default_store()
        # Import here to avoid circular dependency
        from agent_lens.control import ControlPlane
        self._control = ControlPlane.get_instance()
        self._control.set_store(self._store)
        self._bus = EventBus.get_instance()

    @classmethod
    def get_instance(cls) -> "Tracer":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = Tracer()
        return cls._instance

    @classmethod
    def reset(cls, store=None) -> None:
        """Reset the singleton (useful in tests)."""
        with cls._instance_lock:
            cls._instance = None

    def start_run(self, name: str, metadata: dict | None = None) -> Run:
        """Create and persist a new Run."""
        run = Run(
            id=str(uuid.uuid4()),
            name=name,
            start_time=time.time(),
            status=RunStatus.RUNNING,
            metadata=metadata or {},
        )
        self._store.save_run(run)
        TraceContext.set_run_id(run.id)
        self._bus.publish({"type": "run_start", "run": run.model_dump()})
        return run

    def end_run(self, run_id: str, status: RunStatus = RunStatus.COMPLETED) -> None:
        """Mark a run as completed."""
        end_time = time.time()
        self._store.update_run_status(run_id, status, end_time)
        self._bus.publish({"type": "run_end", "run_id": run_id, "status": status.value})
        self._control.cleanup(run_id)
        TraceContext.clear()

    def start_span(
        self,
        name: str,
        span_type: str,
        run_id: str | None = None,
        parent_id: str | None = None,
    ) -> Span:
        """Create and persist a new Span."""
        effective_run_id = run_id or TraceContext.get_run_id()
        if effective_run_id is None:
            # Auto-create a run if none exists
            run = self.start_run(name)
            effective_run_id = run.id

        effective_parent_id = parent_id or TraceContext.get_span_id()

        span = Span(
            id=str(uuid.uuid4()),
            run_id=effective_run_id,
            parent_id=effective_parent_id,
            name=name,
            type=span_type,
            start_time=time.time(),
            status="ok",
        )
        self._store.save_span(span)
        TraceContext.push_span(span.id)

        event = Event(
            run_id=effective_run_id,
            span_id=span.id,
            parent_span_id=effective_parent_id,
            type=EventType.AGENT_START if span_type == "agent" else EventType.LLM_START,
            data={"name": name, "type": span_type},
        )
        self._store.save_event(event)
        self._bus.publish({"type": "span_start", "span": span.model_dump()})
        return span

    def end_span(self, span: Span, status: str = "ok", output: Any = None) -> None:
        """Mark a span as completed and persist it."""
        span.end_time = time.time()
        span.status = status
        self._store.save_span(span)

        event_type = (
            EventType.AGENT_END if span.type == "agent"
            else EventType.ERROR if status == "error"
            else EventType.LLM_END
        )
        event = Event(
            run_id=span.run_id,
            span_id=span.id,
            type=event_type,
            data={"duration_ms": span.duration_ms, "status": status, "output": _redact_value(output)},
        )
        self._store.save_event(event)
        TraceContext.pop_span()
        self._bus.publish({"type": "span_end", "span": span.model_dump()})

    def record_event(
        self,
        event_type: EventType,
        data: dict[str, Any],
        run_id: str | None = None,
        span_id: str | None = None,
        metadata: dict | None = None,
    ) -> Event:
        """Persist an arbitrary event."""
        effective_run_id = run_id or TraceContext.get_run_id() or "unknown"
        effective_span_id = span_id or TraceContext.get_span_id() or "unknown"

        event = Event(
            run_id=effective_run_id,
            span_id=effective_span_id,
            type=event_type,
            data=_redact_value(data),
            metadata=metadata or {},
        )
        self._store.save_event(event)
        self._bus.publish({"type": "event", "event": event.model_dump()})
        return event


# ------------------------------------------------------------------
# @trace decorator
# ------------------------------------------------------------------

def trace(
    func: Callable | None = None,
    *,
    name: str | None = None,
    span_type: str = "agent",
    run_name: str | None = None,
) -> Callable:
    """
    Decorator that wraps a function as a traced Span.

    Usage:
        @trace
        def my_function(): ...

        @trace(name="custom-name", span_type="llm")
        def my_function(): ...
    """

    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__qualname__
        effective_run_name = run_name or fn.__qualname__

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                tracer = Tracer.get_instance()
                is_root = TraceContext.get_run_id() is None
                run = None
                if is_root:
                    run = tracer.start_run(effective_run_name)

                span = tracer.start_span(span_name, span_type)
                try:
                    result = await fn(*args, **kwargs)
                    tracer.end_span(span, status="ok", output=result)
                    if is_root and run:
                        tracer.end_run(run.id, RunStatus.COMPLETED)
                    return result
                except Exception as exc:
                    tracer.end_span(span, status="error", output={"error": str(exc)})
                    tracer.record_event(
                        EventType.ERROR,
                        {"error": str(exc), "type": type(exc).__name__},
                    )
                    if is_root and run:
                        tracer.end_run(run.id, RunStatus.ERROR)
                    raise

            return async_wrapper

        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                tracer = Tracer.get_instance()
                is_root = TraceContext.get_run_id() is None
                run = None
                if is_root:
                    run = tracer.start_run(effective_run_name)

                span = tracer.start_span(span_name, span_type)
                try:
                    result = fn(*args, **kwargs)
                    tracer.end_span(span, status="ok", output=result)
                    if is_root and run:
                        tracer.end_run(run.id, RunStatus.COMPLETED)
                    return result
                except Exception as exc:
                    tracer.end_span(span, status="error", output={"error": str(exc)})
                    tracer.record_event(
                        EventType.ERROR,
                        {"error": str(exc), "type": type(exc).__name__},
                    )
                    if is_root and run:
                        tracer.end_run(run.id, RunStatus.ERROR)
                    raise

            return sync_wrapper

    if func is not None:
        # Called as @trace (no arguments)
        return decorator(func)
    # Called as @trace(...) (with arguments)
    return decorator


@contextmanager
def trace_span(
    name: str,
    span_type: str = "agent",
    run_id: str | None = None,
) -> Generator[Span, None, None]:
    """
    Context manager version of tracing.

    Usage:
        with trace_span("my-operation") as span:
            ...
    """
    tracer = Tracer.get_instance()
    span = tracer.start_span(name, span_type, run_id=run_id)
    try:
        yield span
        tracer.end_span(span, status="ok")
    except Exception as exc:
        tracer.end_span(span, status="error", output={"error": str(exc)})
        raise
