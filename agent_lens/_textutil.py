"""
agent_lens._textutil
~~~~~~~~~~~~~~~~~~~~~
Shared helpers for pulling human-meaningful text out of runs and events.

Both the search index (store) and the diff/export layer (server) consume these,
so the corpus that gets indexed is exactly the corpus that gets compared and
exported — they can never drift apart.
"""

from __future__ import annotations

from typing import Any

from agent_lens.models import Event, Run


def extract_response_text(response: Any) -> str:
    """Best-effort extraction of assistant text from a provider response.

    Handles the raw shapes emitted by the OpenAI and Anthropic integrations
    (dicts keyed by ``content``/``choices``/``text``) and falls back to the
    string form for anything unrecognized.
    """
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return str(response) if response else ""
    for key in ("content", "choices", "text"):
        if key in response:
            val = response[key]
            if isinstance(val, str):
                return val
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, dict):
                    return (
                        first.get("text")
                        or first.get("message", {}).get("content", "")
                        or str(first)
                    )
    return str(response)


def flatten_run_text(run: Run, events: list[Event]) -> str:
    """Concatenate every searchable piece of text for a run into one blob.

    Includes the run name, developer notes, the expected-output assertion, and
    per event: prompt messages, extracted response text, chain-of-thought
    (Anthropic thinking blocks), and error text. All content is already redacted
    at capture time, so no secrets reach the index.
    """
    parts: list[str] = []
    if run.name:
        parts.append(run.name)
    if run.notes:
        parts.append(run.notes)
    if run.expected_output:
        parts.append(run.expected_output)

    for event in events:
        data = event.data or {}
        for message in data.get("messages", []) or []:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "")
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            parts.append(f"{role}: {content}" if role else content)
        if "response" in data:
            text = extract_response_text(data.get("response"))
            if text:
                parts.append(text)
        for block in data.get("thinking_blocks", []) or []:
            parts.append(block if isinstance(block, str) else str(block))
        if "error" in data:
            parts.append(str(data.get("error")))

    return "\n".join(p for p in parts if p)
