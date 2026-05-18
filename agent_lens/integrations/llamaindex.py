"""
agent_lens.integrations.llamaindex
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LlamaIndex callback handler that integrates with agent-lens tracing.

Usage:
    from agent_lens.integrations.llamaindex import AgentLensLlamaIndexHandler
    import agent_lens

    agent_lens.install()
    handler = AgentLensLlamaIndexHandler()

    from llama_index.core import Settings
    Settings.callback_manager = CallbackManager([handler])

Graceful: if llama-index is not installed, AgentLensLlamaIndexHandler is a stub.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from llama_index.core.callbacks.base_handler import BaseCallbackHandler  # type: ignore[import]
    from llama_index.core.callbacks.schema import CBEventType, EventPayload  # type: ignore[import]
    _LLAMAINDEX_AVAILABLE = True
except ImportError:
    _LLAMAINDEX_AVAILABLE = False
    BaseCallbackHandler = object  # type: ignore[misc,assignment]
    CBEventType = None  # type: ignore[assignment]
    EventPayload = None  # type: ignore[assignment]


class AgentLensLlamaIndexHandler(BaseCallbackHandler):
    """
    LlamaIndex callback handler that records LLM and query events
    as agent-lens spans and events.

    If llama-index is not installed, this class is a no-op stub.
    """

    def __init__(self) -> None:
        if _LLAMAINDEX_AVAILABLE:
            super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
        self._spans: dict[str, Any] = {}
        self._start_times: dict[str, float] = {}

    def _get_tracer(self):
        from agent_lens.tracer import Tracer
        return Tracer.get_instance()

    def on_event_start(
        self,
        event_type: Any,
        payload: dict[str, Any] | None = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> str:
        if not _LLAMAINDEX_AVAILABLE:
            return event_id

        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        payload = payload or {}
        event_name = event_type.value if hasattr(event_type, "value") else str(event_type)

        span = tracer.start_span(f"llamaindex.{event_name}", "llm")
        self._spans[event_id] = span
        self._start_times[event_id] = time.time()

        if CBEventType and event_type == CBEventType.LLM:
            messages = payload.get(EventPayload.MESSAGES, []) if EventPayload else []
            tracer.record_event(
                EventType.LLM_START,
                {
                    "provider": "llamaindex",
                    "messages": redact({"messages": [
                        {"role": getattr(m, "role", "user"), "content": str(getattr(m, "content", m))}
                        for m in messages
                    ]})["messages"],
                    "serialized": payload.get(EventPayload.SERIALIZED, {}) if EventPayload else {},
                },
                span_id=span.id,
            )
        elif CBEventType and event_type == CBEventType.QUERY:
            query_str = payload.get(EventPayload.QUERY_STR, "") if EventPayload else ""
            tracer.record_event(
                EventType.AGENT_START,
                {"provider": "llamaindex", "query": redact({"query": query_str})["query"]},
                span_id=span.id,
            )

        return event_id

    def on_event_end(
        self,
        event_type: Any,
        payload: dict[str, Any] | None = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> None:
        if not _LLAMAINDEX_AVAILABLE:
            return

        from agent_lens.models import EventType

        tracer = self._get_tracer()
        payload = payload or {}
        span = self._spans.pop(event_id, None)
        start_time = self._start_times.pop(event_id, time.time())
        latency_ms = (time.time() - start_time) * 1000

        if span is None:
            return

        if CBEventType and event_type == CBEventType.LLM:
            response = payload.get(EventPayload.RESPONSE, None) if EventPayload else None
            usage = getattr(response, "raw", {}) or {}
            token_usage = usage.get("usage", {}) if isinstance(usage, dict) else {}
            tracer.record_event(
                EventType.LLM_END,
                {
                    "provider": "llamaindex",
                    "latency_ms": latency_ms,
                    "total_tokens": token_usage.get("total_tokens", 0),
                    "prompt_tokens": token_usage.get("prompt_tokens", 0),
                    "completion_tokens": token_usage.get("completion_tokens", 0),
                    "response": str(getattr(response, "message", response)),
                },
                span_id=span.id,
            )
        elif CBEventType and event_type == CBEventType.QUERY:
            response = payload.get(EventPayload.RESPONSE, None) if EventPayload else None
            tracer.record_event(
                EventType.AGENT_END,
                {
                    "provider": "llamaindex",
                    "latency_ms": latency_ms,
                    "response": str(response),
                },
                span_id=span.id,
            )

        tracer.end_span(span, status="ok")

    def start_trace(self, trace_id: str | None = None) -> None:
        pass

    def end_trace(
        self,
        trace_id: str | None = None,
        trace_map: dict[str, list[str]] | None = None,
    ) -> None:
        pass
