"""
agent_lens.server
~~~~~~~~~~~~~~~~~
FastAPI application for the agent-lens dashboard.

Security:
- Binds to 127.0.0.1 ONLY (never 0.0.0.0)
- CORS: localhost and 127.0.0.1 only
- CSRF token printed to stdout on start, required in X-Agent-Lens-Token header
  for all mutating endpoints (pause, resume, fork, inject)
- Input: all bodies validated by Pydantic
- Output: HTML exports are HTML-escaped
"""

from __future__ import annotations

import asyncio
import html
import json
import secrets
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_lens.store import get_default_store
from agent_lens.tracer import EventBus

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7878

DASHBOARD_DIR = Path(__file__).parent / "dashboard"

# CSRF token — generated once per process start
CSRF_TOKEN: str = secrets.token_hex(32)


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class ForkBody(BaseModel):
    span_id: str
    edited_messages: list[dict[str, Any]] | None = None
    notes: str | None = None  # Developer hypothesis / reason for this fork
    expected_output: str | None = None  # Assertion: substring that should appear in the response


class NoteBody(BaseModel):
    notes: str


class InjectBody(BaseModel):
    tool_result: Any


# ------------------------------------------------------------------
# CSRF dependency
# ------------------------------------------------------------------

async def require_csrf(x_agent_lens_token: str | None = Header(default=None)) -> None:
    """Dependency that enforces the per-session CSRF token."""
    if x_agent_lens_token != CSRF_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Agent-Lens-Token header")


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def create_app(store=None, csrf_token: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    global CSRF_TOKEN

    if csrf_token is not None:
        CSRF_TOKEN = csrf_token

    effective_store = store or get_default_store()

    app = FastAPI(
        title="agent-lens",
        description="Interactive debugger for LLM agents",
        version="0.1.0",
        docs_url=None,  # Disable Swagger UI in production
        redoc_url=None,
    )

    # CORS: localhost only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:7878",
            "http://127.0.0.1:7878",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------

    if DASHBOARD_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def serve_dashboard():
        index = DASHBOARD_DIR / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>agent-lens dashboard</h1><p>Dashboard files not found.</p>")

    # ------------------------------------------------------------------
    # Runs API
    # ------------------------------------------------------------------

    @app.get("/runs")
    async def list_runs():
        runs = effective_store.get_runs()
        return [r.model_dump() for r in runs]

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
        spans = effective_store.get_spans(run_id)
        return {
            **run.model_dump(),
            "spans": [s.model_dump() for s in spans],
        }

    @app.get("/runs/{run_id}/spans")
    async def get_spans(run_id: str):
        spans = effective_store.get_spans(run_id, include_parent_spans=True)
        # Build tree
        span_map = {s.id: s.model_dump() for s in spans}
        roots = []
        for s in spans:
            if s.parent_id and s.parent_id in span_map:
                parent = span_map[s.parent_id]
                if "children" not in parent:
                    parent["children"] = []
                parent["children"].append(span_map[s.id])
            elif s.parent_id is None:
                roots.append(span_map[s.id])
        return roots or list(span_map.values())

    @app.get("/runs/{run_id}/export")
    async def export_run(run_id: str):
        """Export run as self-contained HTML."""
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        spans = effective_store.get_spans(run_id)
        events = effective_store.get_events(run_id)

        data = {
            "run": run.model_dump(),
            "spans": [s.model_dump() for s in spans],
            "events": [e.model_dump() for e in events],
        }

        # Escape data for safe embedding
        json_data = json.dumps(data, default=str)
        escaped_data = html.escape(json_data)

        css_path = DASHBOARD_DIR / "style.css"
        js_path = DASHBOARD_DIR / "app.js"
        css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
        js = js_path.read_text(encoding="utf-8") if js_path.exists() else ""

        notes_html = ""
        if run.notes:
            escaped_notes = html.escape(run.notes)
            notes_html = f"""<div style="background:#fef9c3;border-left:4px solid #eab308;padding:12px 16px;margin:16px 0;border-radius:6px;font-family:sans-serif">
<strong style="font-size:0.8rem;text-transform:uppercase;letter-spacing:0.06em;color:#92400e">Fork hypothesis</strong>
<p style="margin:6px 0 0;color:#713f12">{escaped_notes}</p>
</div>"""

        export_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>agent-lens export: {html.escape(run.name)}</title>
<style>{css}</style>
</head>
<body>
{notes_html}
<script>
window.__AGENT_LENS_EXPORT__ = true;
window.__AGENT_LENS_DATA__ = JSON.parse(document.getElementById('al-data').textContent);
</script>
<script id="al-data" type="application/json">{escaped_data}</script>
<script>{js}</script>
</body>
</html>"""

        return Response(
            content=export_html,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}.html"'},
        )

    # ------------------------------------------------------------------
    # Control API (CSRF-protected)
    # ------------------------------------------------------------------

    @app.post("/runs/{run_id}/pause", dependencies=[Depends(require_csrf)])
    async def pause_run(run_id: str):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        from agent_lens.control import ControlPlane
        cp = ControlPlane.get_instance()
        cp.set_store(effective_store)
        cp.pause(run_id)
        return {"status": "paused", "run_id": run_id}

    @app.post("/runs/{run_id}/resume", dependencies=[Depends(require_csrf)])
    async def resume_run(run_id: str):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        from agent_lens.control import ControlPlane
        cp = ControlPlane.get_instance()
        cp.set_store(effective_store)
        cp.resume(run_id)
        return {"status": "running", "run_id": run_id}

    @app.post("/runs/{run_id}/step", dependencies=[Depends(require_csrf)])
    async def step_run(run_id: str):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        from agent_lens.control import ControlPlane
        cp = ControlPlane.get_instance()
        cp.set_store(effective_store)
        cp.step(run_id, num_calls=1)
        return {"status": "stepping", "run_id": run_id}

    @app.post("/runs/{run_id}/fork", dependencies=[Depends(require_csrf)])
    async def fork_run(run_id: str, body: ForkBody):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        from agent_lens.control import ControlPlane
        cp = ControlPlane.get_instance()
        cp.set_store(effective_store)
        new_run_id = cp.fork(
            run_id,
            body.span_id,
            edited_messages=body.edited_messages,
            notes=body.notes,
            expected_output=body.expected_output,
            store=effective_store,
        )
        return {"status": "forked", "run_id": run_id, "new_run_id": new_run_id}

    @app.post("/runs/{run_id}/note", dependencies=[Depends(require_csrf)])
    async def add_note(run_id: str, body: NoteBody):
        """Add or update a developer note on any run (not just forks)."""
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
        run.notes = body.notes
        effective_store.save_run(run)
        return {"status": "ok", "run_id": run_id}

    @app.get("/runs/{run_id}/lineage")
    async def get_lineage(run_id: str):
        """Return the full fork ancestry chain for a run, oldest first.

        Each entry has the run summary and its notes/hypothesis, letting you
        trace the evolution of thinking across all fork generations.
        """
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        chain = []
        current = run
        while current is not None:
            chain.append({
                "id": current.id,
                "name": current.name,
                "notes": current.notes,
                "expected_output": current.expected_output,
                "status": current.status,
                "start_time": current.start_time,
                "fork_span_id": current.fork_span_id,
                "depth": len(chain),
            })
            if current.parent_run_id:
                current = effective_store.get_run(current.parent_run_id)
            else:
                current = None

        chain.reverse()  # oldest ancestor first
        return {"run_id": run_id, "lineage": chain, "depth": len(chain)}

    @app.get("/runs/{run_id}/diff/{other_run_id}")
    async def diff_runs(run_id: str, other_run_id: str):
        """Compare two runs structurally: message diff, response diff, metrics delta, assertion result.

        Designed for comparing a fork against its parent to evaluate whether
        a hypothesis was confirmed. Works between any two runs.
        """
        run_a = effective_store.get_run(run_id)
        run_b = effective_store.get_run(other_run_id)
        if run_a is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
        if run_b is None:
            raise HTTPException(status_code=404, detail=f"Run {other_run_id!r} not found")

        events_a = effective_store.get_events(run_id)
        events_b = effective_store.get_events(other_run_id)

        def _first_llm_start(events: list) -> dict:
            for e in events:
                if e.type == "llm_start":
                    return e.data
            return {}

        def _first_llm_end(events: list) -> dict:
            for e in events:
                if e.type == "llm_end":
                    return e.data
            return {}

        def _extract_text(response: dict) -> str:
            """Best-effort extraction of response text."""
            for key in ("content", "choices", "text"):
                if key in response:
                    val = response[key]
                    if isinstance(val, str):
                        return val
                    if isinstance(val, list) and val:
                        first = val[0]
                        if isinstance(first, dict):
                            return (
                                first.get("text") or
                                first.get("message", {}).get("content", "") or
                                str(first)
                            )
            return str(response)

        start_a = _first_llm_start(events_a)
        start_b = _first_llm_start(events_b)
        end_a = _first_llm_end(events_a)
        end_b = _first_llm_end(events_b)

        msgs_a = start_a.get("messages", [])
        msgs_b = start_b.get("messages", [])
        max_len = max(len(msgs_a), len(msgs_b))
        messages_diff = []
        for i in range(max_len):
            ma = msgs_a[i] if i < len(msgs_a) else None
            mb = msgs_b[i] if i < len(msgs_b) else None
            messages_diff.append({
                "index": i,
                "role": (ma or mb or {}).get("role"),
                "a": ma.get("content") if ma else None,
                "b": mb.get("content") if mb else None,
                "changed": ma != mb,
            })

        def _delta(a: float | None, b: float | None) -> dict:
            if a is None or b is None:
                return {"a": a, "b": b, "delta": None, "pct_change": None}
            delta = b - a
            pct = round((delta / a) * 100, 1) if a != 0 else None
            return {"a": a, "b": b, "delta": round(delta, 4), "pct_change": pct}

        metrics_delta = {
            "latency_ms": _delta(end_a.get("latency_ms"), end_b.get("latency_ms")),
            "total_tokens": _delta(
                end_a.get("total_tokens") or (end_a.get("input_tokens", 0) + end_a.get("output_tokens", 0)) or None,
                end_b.get("total_tokens") or (end_b.get("input_tokens", 0) + end_b.get("output_tokens", 0)) or None,
            ),
            "cost_usd": _delta(end_a.get("cost_usd"), end_b.get("cost_usd")),
        }

        resp_a = _extract_text(end_a.get("response", {}))
        resp_b = _extract_text(end_b.get("response", {}))

        assertion_result = None
        expected = run_b.expected_output or run_a.expected_output
        if expected:
            passed_a = expected.lower() in resp_a.lower()
            passed_b = expected.lower() in resp_b.lower()
            assertion_result = {
                "expected_output": expected,
                "passed_in_a": passed_a,
                "passed_in_b": passed_b,
                "verdict": "improved" if (not passed_a and passed_b) else
                           "regressed" if (passed_a and not passed_b) else
                           "both_pass" if (passed_a and passed_b) else "neither_pass",
            }

        return {
            "run_a": {"id": run_a.id, "name": run_a.name, "notes": run_a.notes},
            "run_b": {"id": run_b.id, "name": run_b.name, "notes": run_b.notes},
            "messages_diff": messages_diff,
            "response_diff": {
                "a": resp_a[:2000] if resp_a else None,
                "b": resp_b[:2000] if resp_b else None,
                "changed": resp_a != resp_b,
            },
            "metrics_delta": metrics_delta,
            "thinking_blocks": {
                "a": end_a.get("thinking_blocks", []),
                "b": end_b.get("thinking_blocks", []),
            },
            "assertion_result": assertion_result,
        }

    @app.post("/runs/{run_id}/inject", dependencies=[Depends(require_csrf)])
    async def inject_tool(run_id: str, body: InjectBody):
        run = effective_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

        from agent_lens.control import ControlPlane
        cp = ControlPlane.get_instance()
        cp.inject(run_id, body.tool_result)
        return {"status": "injected", "run_id": run_id}

    # ------------------------------------------------------------------
    # SSE event stream
    # ------------------------------------------------------------------

    @app.get("/events/stream")
    async def event_stream(request: Request, max_events: int = 0):
        """
        Server-Sent Events endpoint. Streams all new events in real time.
        max_events: if > 0, close after that many events (useful for tests).
        """
        bus = EventBus.get_instance()
        bus.set_loop(asyncio.get_event_loop())
        queue = bus.subscribe()

        async def generate() -> AsyncGenerator[str, None]:
            # Ping so the client knows the connection is established
            yield "data: {\"type\": \"ping\"}\n\n"
            if max_events == 1:
                # Test mode: close after initial ping
                bus.unsubscribe(queue)
                return
            try:
                count = 0
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event_data = await asyncio.wait_for(queue.get(), timeout=1.0)
                        yield f"data: {json.dumps(event_data, default=str)}\n\n"
                        count += 1
                        if max_events > 0 and count >= max_events - 1:
                            break
                    except asyncio.TimeoutError:
                        yield "data: {\"type\": \"keepalive\"}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ------------------------------------------------------------------
    # Info endpoint
    # ------------------------------------------------------------------

    @app.get("/info")
    async def info():
        return {
            "version": "0.1.0",
            "host": DEFAULT_HOST,
        }

    return app


# Module-level app for uvicorn
app = create_app()
