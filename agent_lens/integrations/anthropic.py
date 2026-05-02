"""
agent_lens.integrations.anthropic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monkey-patches anthropic.resources.messages.Messages.create and .stream
to automatically capture LLM calls as agent-lens spans.

Graceful: if anthropic is not installed, the patch is a no-op.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_patched = False
_patch_lock = threading.Lock()

# Anthropic pricing (USD per 1M tokens) — approximate
_ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-5-haiku": {"input": 0.8, "output": 4.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for an Anthropic call."""
    pricing = None
    for key in _ANTHROPIC_PRICING:
        if key in model:
            pricing = _ANTHROPIC_PRICING[key]
            break
    if pricing is None:
        return 0.0
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


def _extract_messages(messages: Any) -> list[dict]:
    """Safely extract messages list, redacting secrets."""
    from agent_lens.tracer import redact

    if not isinstance(messages, list):
        return []
    result = []
    for m in messages:
        if isinstance(m, dict):
            result.append(redact(m))
        elif hasattr(m, "model_dump"):
            result.append(redact(m.model_dump()))
        else:
            result.append({"role": "unknown", "content": str(m)})
    return result


def _safe_response_dict(response: Any) -> dict:
    """Convert an Anthropic response object to a safe dict."""
    from agent_lens.tracer import redact

    try:
        if hasattr(response, "model_dump"):
            return redact(response.model_dump())
        if hasattr(response, "__dict__"):
            d = {k: v for k, v in response.__dict__.items() if not k.startswith("_")}
            return redact(d)
    except Exception:
        pass
    return {}


def patch() -> bool:
    """
    Monkey-patch anthropic's Messages.create.
    Returns True if patched, False if anthropic is not installed.
    """
    global _patched

    with _patch_lock:
        if _patched:
            return True

        try:
            import anthropic  # noqa: F401
            from anthropic.resources.messages import Messages
        except ImportError:
            return False

        from agent_lens.models import EventType
        from agent_lens.tracer import TraceContext, Tracer

        _original_create = Messages.create

        def _patched_create(self_inner, *args, **kwargs):
            tracer = Tracer.get_instance()
            control = tracer._control

            run_id = TraceContext.get_run_id() or "unknown"
            injected = control.before_llm_call(run_id)
            if injected is not None:
                return injected

            model = kwargs.get("model", args[0] if args else "unknown")
            messages = kwargs.get("messages", [])
            system = kwargs.get("system")

            span = tracer.start_span(f"anthropic.messages({model})", "llm")
            start_time = time.time()

            event_data: dict[str, Any] = {
                "provider": "anthropic",
                "model": model,
                "messages": _extract_messages(messages),
            }
            if system:
                from agent_lens.tracer import redact
                event_data["system"] = redact({"system": system})["system"]

            tracer.record_event(
                EventType.LLM_START,
                event_data,
                span_id=span.id,
            )

            try:
                response = _original_create(self_inner, *args, **kwargs)
            except Exception as exc:
                tracer.end_span(span, status="error", output={"error": str(exc)})
                raise

            latency_ms = (time.time() - start_time) * 1000

            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

            tracer.record_event(
                EventType.LLM_END,
                {
                    "provider": "anthropic",
                    "model": model,
                    "latency_ms": latency_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": _estimate_cost(model, input_tokens, output_tokens),
                    "response": _safe_response_dict(response),
                },
                span_id=span.id,
            )
            tracer.end_span(span, status="ok")
            return response

        Messages.create = _patched_create

        # Also patch async create if available
        if hasattr(Messages, "acreate"):
            _original_acreate = Messages.acreate

            async def _patched_acreate(self_inner, *args, **kwargs):
                tracer = Tracer.get_instance()
                control = tracer._control

                run_id = TraceContext.get_run_id() or "unknown"
                injected = control.before_llm_call(run_id)
                if injected is not None:
                    return injected

                model = kwargs.get("model", args[0] if args else "unknown")
                messages = kwargs.get("messages", [])
                span = tracer.start_span(f"anthropic.acreate({model})", "llm")
                start_time = time.time()

                tracer.record_event(
                    EventType.LLM_START,
                    {"provider": "anthropic", "model": model, "messages": _extract_messages(messages)},
                    span_id=span.id,
                )

                try:
                    response = await _original_acreate(self_inner, *args, **kwargs)
                except Exception as exc:
                    tracer.end_span(span, status="error", output={"error": str(exc)})
                    raise

                latency_ms = (time.time() - start_time) * 1000
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

                tracer.record_event(
                    EventType.LLM_END,
                    {
                        "provider": "anthropic",
                        "model": model,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": _estimate_cost(model, input_tokens, output_tokens),
                        "response": _safe_response_dict(response),
                    },
                    span_id=span.id,
                )
                tracer.end_span(span, status="ok")
                return response

            Messages.acreate = _patched_acreate

        _patched = True
        return True


def unpatch() -> None:
    """Remove the monkey-patch (mainly for testing)."""
    global _patched
    with _patch_lock:
        _patched = False
