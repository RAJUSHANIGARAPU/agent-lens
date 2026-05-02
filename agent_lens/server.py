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

        export_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>agent-lens export: {html.escape(run.name)}</title>
<style>{css}</style>
</head>
<body>
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
            store=effective_store,
        )
        return {"status": "forked", "run_id": run_id, "new_run_id": new_run_id}

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
