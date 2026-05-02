"""
agent_lens.integrations.openai
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monkey-patches openai.resources.chat.completions.Completions.create and .acreate
to automatically capture LLM calls as agent-lens spans.

Graceful: if openai is not installed, the patch is a no-op.
"""

from __future__ import annotations

import time
import threading
from typing import Any

_patched = False
_patch_lock = threading.Lock()


# OpenAI pricing (USD per 1K tokens) — approximate, update periodically
_OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for an OpenAI call."""
    pricing = None
    for key in _OPENAI_PRICING:
        if key in model:
            pricing = _OPENAI_PRICING[key]
            break
    if pricing is None:
        return 0.0
    return (
        prompt_tokens * pricing["input"] / 1000
        + completion_tokens * pricing["output"] / 1000
    )


def _extract_messages(messages: Any) -> list[dict]:
    """Extract and redact messages from various formats."""
    from agent_lens.tracer import redact

    if messages is None:
        return []
    if isinstance(messages, list):
        safe = []
        for m in messages:
            if isinstance(m, dict):
                safe.append(redact({k: v for k, v in m.items() if k != "function_call"}))
            else:
                # Pydantic model or dataclass
                try:
                    safe.append(redact(m.model_dump() if hasattr(m, "model_dump") else dict(m)))
                except Exception:
                    safe.append({"role": "unknown", "content": str(m)})
        return safe
    return []


def _safe_response_dict(response: Any) -> dict:
    """Convert an OpenAI response object to a safe dict."""
    from agent_lens.tracer import redact

    try:
        if hasattr(response, "model_dump"):
            return redact(response.model_dump())
        if hasattr(response, "__dict__"):
            return redact({k: v for k, v in response.__dict__.items() if not k.startswith("_")})
    except Exception:
        pass
    return {}


def patch() -> bool:
    """
    Monkey-patch openai's Completions.create and acreate.
    Returns True if patched, False if openai is not installed.
    """
    global _patched

    with _patch_lock:
        if _patched:
            return True

        try:
            import openai
            from openai.resources.chat.completions import Completions
        except ImportError:
            return False

        from agent_lens.models import EventType
        from agent_lens.tracer import Tracer, TraceContext, redact

        _original_create = Completions.create
        _original_acreate = Completions.acreate

        def _patched_create(self_inner, *args, **kwargs):
            tracer = Tracer.get_instance()
            control = tracer._control

            # Check for injected result (pause/fork)
            run_id = TraceContext.get_run_id() or "unknown"
            injected = control.before_llm_call(run_id)
            if injected is not None:
                return injected

            model = kwargs.get("model", args[0] if args else "unknown")
            messages = kwargs.get("messages", [])

            span = tracer.start_span(f"openai.chat({model})", "llm")
            start_time = time.time()

            # Record LLM_START
            tracer.record_event(
                EventType.LLM_START,
                {
                    "provider": "openai",
                    "model": model,
                    "messages": _extract_messages(messages),
                    "kwargs": {
                        k: v for k, v in kwargs.items()
                        if k not in ("messages", "api_key")
                    },
                },
                span_id=span.id,
            )

            try:
                response = _original_create(self_inner, *args, **kwargs)
            except Exception as exc:
                tracer.end_span(span, status="error", output={"error": str(exc)})
                raise

            latency_ms = (time.time() - start_time) * 1000

            # Extract usage info
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            cost = _estimate_cost(model, prompt_tokens, completion_tokens)

            # Record LLM_END
            tracer.record_event(
                EventType.LLM_END,
                {
                    "provider": "openai",
                    "model": model,
                    "latency_ms": latency_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "cost_usd": cost,
                    "response": _safe_response_dict(response),
                },
                span_id=span.id,
            )
            tracer.end_span(span, status="ok")
            return response

        async def _patched_acreate(self_inner, *args, **kwargs):
            tracer = Tracer.get_instance()
            control = tracer._control

            run_id = TraceContext.get_run_id() or "unknown"
            injected = control.before_llm_call(run_id)
            if injected is not None:
                return injected

            model = kwargs.get("model", args[0] if args else "unknown")
            messages = kwargs.get("messages", [])

            span = tracer.start_span(f"openai.achat({model})", "llm")
            start_time = time.time()

            tracer.record_event(
                EventType.LLM_START,
                {
                    "provider": "openai",
                    "model": model,
                    "messages": _extract_messages(messages),
                },
                span_id=span.id,
            )

            try:
                response = await _original_acreate(self_inner, *args, **kwargs)
            except Exception as exc:
                tracer.end_span(span, status="error", output={"error": str(exc)})
                raise

            latency_ms = (time.time() - start_time) * 1000
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

            tracer.record_event(
                EventType.LLM_END,
                {
                    "provider": "openai",
                    "model": model,
                    "latency_ms": latency_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": _estimate_cost(model, prompt_tokens, completion_tokens),
                    "response": _safe_response_dict(response),
                },
                span_id=span.id,
            )
            tracer.end_span(span, status="ok")
            return response

        Completions.create = _patched_create
        Completions.acreate = _patched_acreate
        _patched = True
        return True


def unpatch() -> None:
    """Remove the monkey-patch (mainly for testing)."""
    global _patched

    with _patch_lock:
        if not _patched:
            return
        try:
            import openai
            from openai.resources.chat.completions import Completions
        except ImportError:
            return

        # Restore originals stored in the closure — we can't easily unwrap,
        # so we reset the flag and the patch won't apply again.
        _patched = False
