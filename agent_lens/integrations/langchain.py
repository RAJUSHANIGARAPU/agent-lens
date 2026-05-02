"""
agent_lens.integrations.langchain
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LangChain callback handler that integrates with agent-lens tracing.

Usage:
    from agent_lens.integrations.langchain import AgentLensCallbackHandler
    handler = AgentLensCallbackHandler()
    llm = ChatOpenAI(callbacks=[handler])

Graceful: if langchain is not installed, AgentLensCallbackHandler is a stub class.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

# Attempt to import LangChain base class; fall back to a plain object if not installed
try:
    from langchain_core.callbacks.base import BaseCallbackHandler  # type: ignore[import]
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    try:
        from langchain.callbacks.base import BaseCallbackHandler  # type: ignore[import]
        _LANGCHAIN_AVAILABLE = True
    except ImportError:
        _LANGCHAIN_AVAILABLE = False
        BaseCallbackHandler = object  # type: ignore[misc,assignment]


class AgentLensCallbackHandler(BaseCallbackHandler):
    """
    LangChain callback handler that records LLM, tool, and chain events
    as agent-lens spans and events.

    If LangChain is not installed, this class is a no-op stub.
    """

    def __init__(self) -> None:
        if _LANGCHAIN_AVAILABLE:
            super().__init__()
        # Track active spans by run_id (LangChain's UUID, not agent-lens run_id)
        self._spans: dict[str, Any] = {}
        self._start_times: dict[str, float] = {}

    def _get_tracer(self):
        from agent_lens.tracer import Tracer
        return Tracer.get_instance()

    # ------------------------------------------------------------------
    # LLM callbacks
    # ------------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])
        span = tracer.start_span(f"llm.{model_name}", "llm")
        self._spans[str(run_id)] = span
        self._start_times[str(run_id)] = time.time()

        tracer.record_event(
            EventType.LLM_START,
            {
                "provider": "langchain",
                "model": model_name,
                "prompts": [redact({"prompt": p})["prompt"] for p in prompts],
                "serialized": serialized,
            },
            span_id=span.id,
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        start_time = self._start_times.pop(str(run_id), time.time())
        latency_ms = (time.time() - start_time) * 1000

        if span is None:
            return

        # Extract token usage from LangChain response
        llm_output = getattr(response, "llm_output", {}) or {}
        token_usage = llm_output.get("token_usage", {})

        tracer.record_event(
            EventType.LLM_END,
            {
                "provider": "langchain",
                "latency_ms": latency_ms,
                "prompt_tokens": token_usage.get("prompt_tokens", 0),
                "completion_tokens": token_usage.get("completion_tokens", 0),
                "total_tokens": token_usage.get("total_tokens", 0),
                "generations": [
                    [{"text": g.text} for g in gen]
                    for gen in (getattr(response, "generations", []) or [])
                ],
            },
            span_id=span.id,
        )
        tracer.end_span(span, status="ok")

    def on_llm_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        self._start_times.pop(str(run_id), None)

        if span is None:
            return

        tracer.record_event(
            EventType.ERROR,
            {"error": str(error), "type": type(error).__name__},
            span_id=span.id,
        )
        tracer.end_span(span, status="error", output={"error": str(error)})

    # ------------------------------------------------------------------
    # Tool callbacks
    # ------------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        tool_name = serialized.get("name", "unknown_tool")
        span = tracer.start_span(f"tool.{tool_name}", "tool")
        self._spans[str(run_id)] = span
        self._start_times[str(run_id)] = time.time()

        tracer.record_event(
            EventType.TOOL_START,
            {
                "tool": tool_name,
                "input": redact({"input": input_str})["input"],
            },
            span_id=span.id,
        )

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        start_time = self._start_times.pop(str(run_id), time.time())
        latency_ms = (time.time() - start_time) * 1000

        if span is None:
            return

        tracer.record_event(
            EventType.TOOL_END,
            {
                "output": redact({"output": output})["output"],
                "latency_ms": latency_ms,
            },
            span_id=span.id,
        )
        tracer.end_span(span, status="ok")

    def on_tool_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        self._start_times.pop(str(run_id), None)

        if span is None:
            return

        tracer.record_event(
            EventType.ERROR,
            {"error": str(error), "type": type(error).__name__},
            span_id=span.id,
        )
        tracer.end_span(span, status="error", output={"error": str(error)})

    # ------------------------------------------------------------------
    # Chain callbacks
    # ------------------------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        chain_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])
        span = tracer.start_span(f"chain.{chain_name}", "agent")
        self._spans[str(run_id)] = span
        self._start_times[str(run_id)] = time.time()

        tracer.record_event(
            EventType.AGENT_START,
            {
                "chain": chain_name,
                "inputs": redact(inputs),
            },
            span_id=span.id,
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType
        from agent_lens.tracer import redact

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        start_time = self._start_times.pop(str(run_id), time.time())
        latency_ms = (time.time() - start_time) * 1000

        if span is None:
            return

        tracer.record_event(
            EventType.AGENT_END,
            {
                "outputs": redact(outputs),
                "latency_ms": latency_ms,
            },
            span_id=span.id,
        )
        tracer.end_span(span, status="ok")

    def on_chain_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if not _LANGCHAIN_AVAILABLE:
            return
        from agent_lens.models import EventType

        tracer = self._get_tracer()
        span = self._spans.pop(str(run_id), None)
        self._start_times.pop(str(run_id), None)

        if span is None:
            return

        tracer.record_event(
            EventType.ERROR,
            {"error": str(error), "type": type(error).__name__},
            span_id=span.id,
        )
        tracer.end_span(span, status="error", output={"error": str(error)})
