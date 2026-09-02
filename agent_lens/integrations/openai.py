"""
agent_lens.integrations.openai
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monkey-patches the OpenAI SDK's chat-completions entry points to automatically
capture LLM calls as agent-lens spans.

Two call sites are patched, when the installed SDK exposes them:

- ``Completions.create``      — the synchronous client
- ``AsyncCompletions.create`` — the asynchronous client

Graceful: if openai is not installed, the patch is a no-op. A call site the
installed SDK does not expose is skipped rather than raising, so a future SDK
reshuffle degrades to reduced capture instead of an import-time crash.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from agent_lens.integrations import _pricing

_patched = False
_patch_lock = threading.Lock()

# Original callables we replaced, keyed by (owner class, attribute name), so
# unpatch() can genuinely restore them. Without this, unpatch() only clears the
# flag and the next patch() wraps the already-wrapped function, duplicating
# every span and event.
_originals: dict[tuple[type, str], Any] = {}


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
    return _pricing.estimate_cost(
        _OPENAI_PRICING, model, prompt_tokens, completion_tokens, per_tokens=1_000
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


def _start_data(model: str, messages: Any, kwargs: dict) -> dict:
    """Build the LLM_START payload."""
    return {
        "provider": "openai",
        "model": model,
        "messages": _extract_messages(messages),
        "kwargs": {k: v for k, v in kwargs.items() if k not in ("messages", "api_key")},
    }


def _end_data(model: str, response: Any, latency_ms: float) -> dict:
    """Build the LLM_END payload."""
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    return {
        "provider": "openai",
        "model": model,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": _estimate_cost(model, prompt_tokens, completion_tokens),
        "response": _safe_response_dict(response),
    }


def patch() -> bool:
    """
    Monkey-patch the OpenAI SDK's chat-completions call sites.

    Returns True if patched, False if openai is not installed.
    """
    global _patched

    with _patch_lock:
        if _patched:
            return True

        try:
            import openai  # noqa: F401
            from openai.resources.chat import completions as _completions
        except ImportError:
            return False

        from agent_lens.models import EventType
        from agent_lens.tracer import TraceContext, Tracer

        def _make_sync(original, span_prefix: str):
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

                span = tracer.start_span(f"{span_prefix}({model})", "llm")
                start_time = time.time()

                tracer.record_event(
                    EventType.LLM_START,
                    _start_data(model, messages, kwargs),
                    span_id=span.id,
                )

                try:
                    response = original(self_inner, *args, **kwargs)
                except Exception as exc:
                    tracer.end_span(span, status="error", output={"error": str(exc)})
                    raise

                latency_ms = (time.time() - start_time) * 1000
                tracer.record_event(
                    EventType.LLM_END,
                    _end_data(model, response, latency_ms),
                    span_id=span.id,
                )
                tracer.end_span(span, status="ok")
                return response

            return _patched_create

        def _make_async(original, span_prefix: str):
            async def _patched_acreate(self_inner, *args, **kwargs):
                tracer = Tracer.get_instance()
                control = tracer._control

                run_id = TraceContext.get_run_id() or "unknown"
                injected = control.before_llm_call(run_id)
                if injected is not None:
                    return injected

                model = kwargs.get("model", args[0] if args else "unknown")
                messages = kwargs.get("messages", [])

                span = tracer.start_span(f"{span_prefix}({model})", "llm")
                start_time = time.time()

                tracer.record_event(
                    EventType.LLM_START,
                    _start_data(model, messages, kwargs),
                    span_id=span.id,
                )

                try:
                    response = await original(self_inner, *args, **kwargs)
                except Exception as exc:
                    tracer.end_span(span, status="error", output={"error": str(exc)})
                    raise

                latency_ms = (time.time() - start_time) * 1000
                tracer.record_event(
                    EventType.LLM_END,
                    _end_data(model, response, latency_ms),
                    span_id=span.id,
                )
                tracer.end_span(span, status="ok")
                return response

            return _patched_acreate

        # (owner attribute on the completions module, method, factory, span name).
        # Every target is optional: the sync and async classes have been renamed
        # and relocated across SDK majors, and a missing one must degrade to
        # reduced capture rather than break the caller's import.
        targets = (
            ("Completions", "create", _make_sync, "openai.chat"),
            ("AsyncCompletions", "create", _make_async, "openai.achat"),
        )

        patched_any = False
        for owner_name, attr, factory, span_prefix in targets:
            owner = getattr(_completions, owner_name, None)
            if owner is None:
                continue
            original = getattr(owner, attr, None)
            if original is None:
                continue

            _originals[(owner, attr)] = original
            setattr(owner, attr, factory(original, span_prefix))
            patched_any = True

        _patched = patched_any
        return patched_any


def unpatch() -> None:
    """Restore the original SDK callables. Safe to call when not patched."""
    global _patched

    with _patch_lock:
        for (owner, attr), original in _originals.items():
            setattr(owner, attr, original)
        _originals.clear()
        _patched = False
