"""
agent_lens.export
~~~~~~~~~~~~~~~~~~
Builders that turn stored runs into records for external search tools.

Two formats:

* ``ndjson`` — provider-neutral: one document per run carrying the flattened
  searchable text plus structured outcome labels (status, assertion result,
  cost, tokens). Keeps the signal that makes agent-lens records richer than raw
  transcripts.
* ``codex`` — a compatibility shim emitting Codex-format session records so
  agent-lens runs can be pulled into ``ctx`` (``ctx import --path`` parses a path
  as Codex format). Structured labels do not survive this mapping.

Kept free of any web-framework imports so the CLI can reuse it without pulling
in FastAPI.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from agent_lens._textutil import extract_response_text, flatten_run_text
from agent_lens.models import Run

# Version reported in Codex-format export metadata (cosmetic).
EXPORT_TOOL_VERSION = "0.2.0"

# Page size used when walking the full run set for corpus export.
_EXPORT_PAGE_SIZE = 500

VALID_FORMATS = ("ndjson", "codex")


def iso(ts: float | None) -> str | None:
    """Render an epoch timestamp as an ISO-8601 UTC string (…Z)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _first_llm_end_data(events: list) -> dict:
    """Return the data of the first llm_end event, or an empty dict."""
    return next((e.data for e in events if e.type == "llm_end"), {})


def assertion_passed(run: Run, events: list) -> bool | None:
    """Whether the run's own expected_output appears in its first response.

    Mirrors the substring semantics of the diff endpoint. Returns None when the
    run has no assertion — deliberately a single-run pass/fail, not the
    comparative improved/regressed verdict (that stays in /diff, which needs a pair).
    """
    if not run.expected_output:
        return None
    resp = extract_response_text(_first_llm_end_data(events).get("response", {}))
    return run.expected_output.lower() in resp.lower()


def ctx_document(store, run: Run) -> dict[str, Any]:
    """Build the provider-neutral ctx document for a run.

    ``text`` is the flattened searchable corpus; ``metadata`` carries the
    structured outcome labels that make agent-lens records higher-signal than
    raw transcripts.
    """
    events = store.get_events(run.id)
    end = _first_llm_end_data(events)
    total_tokens = end.get("total_tokens") or (
        (end.get("input_tokens", 0) + end.get("output_tokens", 0)) or None
    )
    return {
        "id": run.id,
        "source": "agent-lens",
        "title": run.name,
        "text": flatten_run_text(run, events),
        "metadata": {
            "run_id": run.id,
            "status": getattr(run.status, "value", run.status),
            "parent_run_id": run.parent_run_id,
            "fork_span_id": run.fork_span_id,
            "is_fork": run.is_fork,
            "notes": run.notes,
            "expected_output": run.expected_output,
            "assertion_passed": assertion_passed(run, events),
            "started_at": iso(run.start_time),
            "ended_at": iso(run.end_time),
            "start_time": run.start_time,
            "duration_ms": run.duration_ms,
            "total_tokens": total_tokens,
            "cost_usd": end.get("cost_usd"),
            "num_events": len(events),
            "url": f"/runs/{run.id}",
        },
    }


def codex_records(store, run: Run) -> list[dict[str, Any]]:
    """Represent a run as Codex-format session records (one dict per JSONL line)."""
    events = store.get_events(run.id)
    started = iso(run.start_time)
    provider = run.metadata.get("provider", "") if isinstance(run.metadata, dict) else ""

    records: list[dict[str, Any]] = [
        {
            "timestamp": started,
            "type": "session_meta",
            "payload": {
                "id": run.id,
                "timestamp": started,
                "cwd": "",
                "originator": "agent-lens",
                "cli_version": EXPORT_TOOL_VERSION,
                "source": "agent-lens",
                "model_provider": provider,
            },
        }
    ]

    for event in events:
        ts = iso(event.timestamp)
        data = event.data or {}
        if event.type == "llm_start":
            for message in data.get("messages", []) or []:
                if not isinstance(message, dict):
                    continue
                content = message.get("content", "")
                content = content if isinstance(content, str) else str(content)
                records.append({
                    "timestamp": ts,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": message.get("role", "user"),
                        "content": [{"type": "input_text", "text": content}],
                    },
                })
        elif event.type == "llm_end":
            for block in data.get("thinking_blocks", []) or []:
                text = block if isinstance(block, str) else str(block)
                records.append({
                    "timestamp": ts,
                    "type": "response_item",
                    "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": text}]},
                })
            resp = extract_response_text(data.get("response", {}))
            if resp:
                records.append({
                    "timestamp": ts,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": resp}],
                    },
                })
                records.append({
                    "timestamp": ts,
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": resp},
                })
        elif event.type == "tool_start":
            arguments = data.get("arguments") or data.get("input") or {}
            records.append({
                "timestamp": ts,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": data.get("name") or data.get("tool") or "tool",
                    "arguments": json.dumps(arguments, default=str),
                    "call_id": data.get("call_id") or event.span_id,
                },
            })
        elif event.type == "tool_end":
            records.append({
                "timestamp": ts,
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": data.get("call_id") or event.span_id,
                    "output": str(data.get("output") or data.get("result") or ""),
                    "status": "completed",
                },
            })

    return records


def ctx_lines(store, run: Run, fmt: str) -> Iterator[str]:
    """Yield newline-terminated JSON lines for a run in the requested format."""
    if fmt == "codex":
        for record in codex_records(store, run):
            yield json.dumps(record, default=str) + "\n"
    else:
        yield json.dumps(ctx_document(store, run), default=str) + "\n"


def iter_runs(store, status: str | None, limit: int | None) -> Iterator[Run]:
    """Yield runs newest-first, paging so the 100-row default never truncates."""
    yielded = 0
    offset = 0
    while True:
        page = store.get_runs(limit=_EXPORT_PAGE_SIZE, offset=offset)
        if not page:
            return
        for run in page:
            if status and getattr(run.status, "value", run.status) != status:
                continue
            yield run
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        offset += _EXPORT_PAGE_SIZE
