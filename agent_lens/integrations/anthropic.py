"""
agent_lens.integrations.anthropic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monkey-patches the Anthropic SDK's messages entry points to automatically
capture LLM calls as agent-lens spans.

Two call sites are patched, when the installed SDK exposes them:

- ``Messages.create``      — the synchronous client
- ``AsyncMessages.create`` — the asynchronous client

Graceful: if anthropic is not installed, the patch is a no-op. A call site the
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
# unpatch() can genuinely restore them rather than only clearing the flag.
_originals: dict[tuple[type, str], Any] = {}

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
    return _pricing.estimate_cost(
        _ANTHROPIC_PRICING, model, input_tokens, output_tokens, per_tokens=1_000_000
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
            continue
        # A message object that refuses to serialise must not take the caller's
        # LLM call down with it — describe it and move on.
        try:
            result.append(redact(m.model_dump() if hasattr(m, "model_dump") else dict(m)))
        except Exception:
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


def _extract_thinking_blocks(response: Any) -> list[str]:
    """Extract extended thinking blocks from an Anthropic response, if present."""
    blocks = []
    try:
        content = getattr(response, "content", []) or []
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking_text = getattr(block, "thinking", None)
                if thinking_text:
                    blocks.append(thinking_text)
    except Exception:
        pass
    return blocks


def _start_data(model: str, messages: Any, system: Any) -> dict:
    """Build the LLM_START payload."""
    from agent_lens.tracer import redact

    data: dict[str, Any] = {
        "provider": "anthropic",
        "model": model,
        "messages": _extract_messages(messages),
    }
    if system:
        data["system"] = redact({"system": system})["system"]
    return data


def _end_data(model: str, response: Any, latency_ms: float) -> dict:
    """Build the LLM_END payload."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    data: dict[str, Any] = {
        "provider": "anthropic",
        "model": model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_cost(model, input_tokens, output_tokens),
        "response": _safe_response_dict(response),
    }
    thinking_blocks = _extract_thinking_blocks(response)
    if thinking_blocks:
        data["thinking_blocks"] = thinking_blocks
    return data


def patch() -> bool:
    """
    Monkey-patch the Anthropic SDK's messages call sites.

    Returns True if patched, False if anthropic is not installed.
    """
    global _patched

    with _patch_lock:
        if _patched:
            return True

        try:
            import anthropic  # noqa: F401
            from anthropic.resources import messages as _messages
        except ImportError:
            return False

        from agent_lens.models import EventType
        from agent_lens.tracer import TraceContext, Tracer

        def _make_sync(original, span_prefix: str):
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

                span = tracer.start_span(f"{span_prefix}({model})", "llm")
                start_time = time.time()

                tracer.record_event(
                    EventType.LLM_START,
                    _start_data(model, messages, system),
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
                system = kwargs.get("system")

                span = tracer.start_span(f"{span_prefix}({model})", "llm")
                start_time = time.time()

                tracer.record_event(
                    EventType.LLM_START,
                    _start_data(model, messages, system),
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

        # Every target is optional — see the note in the openai integration.
        targets = (
            ("Messages", "create", _make_sync, "anthropic.messages"),
            ("AsyncMessages", "create", _make_async, "anthropic.amessages"),
        )

        patched_any = False
        for owner_name, attr, factory, span_prefix in targets:
            owner = getattr(_messages, owner_name, None)
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
