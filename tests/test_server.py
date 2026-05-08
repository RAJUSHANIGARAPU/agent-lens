"""
Tests for agent_lens.server (FastAPI application)

Covers:
- GET /runs returns empty list initially
- POST /runs/{id}/pause returns 200 and run is paused
- Server binds to 127.0.0.1 by default
- SSE endpoint connects and streams events
"""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from agent_lens.models import Run, RunStatus, Span
from agent_lens.server import DEFAULT_HOST, create_app


@pytest.fixture
def app_and_store(tmp_path):
    """Create a fresh app with a temp store for each test."""
    from agent_lens.store import Store

    db_path = tmp_path / "server_test.db"
    store = Store(path=db_path)

    # Use a fixed CSRF token for tests
    csrf = "test-csrf-token-1234"
    app = create_app(store=store, csrf_token=csrf)
    return app, store, csrf


@pytest.fixture
def client(app_and_store):
    """Return a synchronous test client."""
    app, store, csrf = app_and_store
    with TestClient(app, raise_server_exceptions=True) as c:
        c.headers["X-Agent-Lens-Token"] = csrf
        yield c, store, csrf


# ----------------------------------------------------------------
# Basic API tests
# ----------------------------------------------------------------

class TestRunsAPI:
    def test_get_runs_empty(self, client):
        c, store, csrf = client
        resp = c.get("/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_runs_returns_runs(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="test-run", start_time=time.time())
        store.save_run(run)

        resp = c.get("/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test-run"

    def test_get_run_not_found(self, client):
        c, store, csrf = client
        resp = c.get("/runs/nonexistent-id")
        assert resp.status_code == 404

    def test_get_run_detail(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="detail-run", start_time=time.time())
        store.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="test-span",
            type="agent",
            start_time=time.time(),
        )
        store.save_span(span)

        resp = c.get(f"/runs/{run.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "detail-run"
        assert len(data["spans"]) == 1

    def test_get_spans(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="spans-run", start_time=time.time())
        store.save_run(run)

        for i in range(3):
            span = Span(
                id=str(uuid.uuid4()),
                run_id=run.id,
                name=f"span-{i}",
                type="llm",
                start_time=time.time() + i * 0.01,
            )
            store.save_span(span)

        resp = c.get(f"/runs/{run.id}/spans")
        assert resp.status_code == 200
        spans = resp.json()
        assert len(spans) >= 1  # May be tree-structured


# ----------------------------------------------------------------
# Control API tests
# ----------------------------------------------------------------

class TestControlAPI:
    def test_pause_returns_200(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="pause-run", start_time=time.time(), status=RunStatus.RUNNING)
        store.save_run(run)

        resp = c.post(f"/runs/{run.id}/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paused"
        assert data["run_id"] == run.id

    def test_pause_updates_store(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="pause-store-run", start_time=time.time())
        store.save_run(run)

        c.post(f"/runs/{run.id}/pause")

        updated = store.get_run(run.id)
        assert updated.status == RunStatus.PAUSED

    def test_resume_returns_200(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="resume-run", start_time=time.time(), status=RunStatus.PAUSED)
        store.save_run(run)
        store.update_run_status(run.id, RunStatus.PAUSED)

        resp = c.post(f"/runs/{run.id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"

    def test_step_returns_200(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="step-run", start_time=time.time())
        store.save_run(run)

        resp = c.post(f"/runs/{run.id}/step")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stepping"

    def test_fork_creates_new_run(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="fork-run", start_time=time.time())
        store.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="span-to-fork-from",
            type="llm",
            start_time=time.time(),
        )
        store.save_span(span)

        resp = c.post(
            f"/runs/{run.id}/fork",
            json={"span_id": span.id, "edited_messages": [{"role": "user", "content": "edited"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "forked"
        assert "new_run_id" in data

        # New run should be in the store
        new_run = store.get_run(data["new_run_id"])
        assert new_run is not None
        assert new_run.parent_run_id == run.id

    def test_fork_with_notes_stored(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="fork-notes-run", start_time=time.time())
        store.save_run(run)
        span = Span(id=str(uuid.uuid4()), run_id=run.id, name="span", type="llm", start_time=time.time())
        store.save_span(span)

        resp = c.post(
            f"/runs/{run.id}/fork",
            json={"span_id": span.id, "notes": "Hypothesis: removing role constraint reduces hallucination"},
        )
        assert resp.status_code == 200
        new_run = store.get_run(resp.json()["new_run_id"])
        assert new_run.notes == "Hypothesis: removing role constraint reduces hallucination"

    def test_add_note_to_run(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="note-run", start_time=time.time())
        store.save_run(run)

        resp = c.post(f"/runs/{run.id}/note", json={"notes": "Trying a shorter system prompt"})
        assert resp.status_code == 200
        updated = store.get_run(run.id)
        assert updated.notes == "Trying a shorter system prompt"

    def test_inject_returns_200(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="inject-run", start_time=time.time())
        store.save_run(run)

        resp = c.post(
            f"/runs/{run.id}/inject",
            json={"tool_result": {"output": "mocked response"}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "injected"

    def test_pause_not_found(self, client):
        c, store, csrf = client
        resp = c.post("/runs/nonexistent/pause")
        assert resp.status_code == 404

    def test_csrf_protection(self, app_and_store):
        """Mutating endpoints require the CSRF token."""
        app, store, csrf = app_and_store
        with TestClient(app) as c:
            # No CSRF token header
            run = Run(id=str(uuid.uuid4()), name="csrf-test", start_time=time.time())
            store.save_run(run)

            resp = c.post(f"/runs/{run.id}/pause")
            assert resp.status_code == 403

    def test_csrf_wrong_token(self, app_and_store):
        """Wrong CSRF token returns 403."""
        app, store, csrf = app_and_store
        with TestClient(app) as c:
            run = Run(id=str(uuid.uuid4()), name="csrf-wrong", start_time=time.time())
            store.save_run(run)

            resp = c.post(
                f"/runs/{run.id}/pause",
                headers={"X-Agent-Lens-Token": "wrong-token"},
            )
            assert resp.status_code == 403


# ----------------------------------------------------------------
# Security: server host config
# ----------------------------------------------------------------

class TestSecurityConfig:
    def test_default_host_is_localhost(self):
        """The default host is 127.0.0.1, never 0.0.0.0."""
        assert DEFAULT_HOST == "127.0.0.1"
        assert DEFAULT_HOST != "0.0.0.0"

    def test_app_has_cors_restricted_to_localhost(self, app_and_store):
        """CORS is restricted to localhost origins — verified via response headers."""
        app, store, csrf = app_and_store
        with TestClient(app) as c:
            resp = c.get(
                "/runs",
                headers={"Origin": "http://evil.example.com"},
            )
            allow_origin = resp.headers.get("access-control-allow-origin", "")
            assert "evil.example.com" not in allow_origin


# ----------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------

class TestDashboard:
    def test_root_returns_html(self, client):
        c, store, csrf = client
        resp = c.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_info_endpoint(self, client):
        c, store, csrf = client
        resp = c.get("/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert data["host"] == "127.0.0.1"


# ----------------------------------------------------------------
# Export
# ----------------------------------------------------------------

class TestExport:
    def test_export_run_as_html(self, client):
        c, store, csrf = client
        run = Run(
            id=str(uuid.uuid4()),
            name="export-test-run",
            start_time=time.time(),
            status=RunStatus.COMPLETED,
        )
        store.save_run(run)

        resp = c.get(f"/runs/{run.id}/export")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "export-test-run" in body
        assert "agent-lens" in body.lower()

    def test_export_escapes_xss(self, client):
        """HTML export escapes malicious content from run names."""
        c, store, csrf = client
        # Simulate a run with XSS in name (shouldn't happen but test defensively)
        run = Run(
            id=str(uuid.uuid4()),
            name='<script>alert(1)</script>',
            start_time=time.time(),
        )
        store.save_run(run)

        resp = c.get(f"/runs/{run.id}/export")
        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


# ----------------------------------------------------------------
# SSE stream (async)
# ----------------------------------------------------------------

def test_sse_connects_and_receives_ping(tmp_path):
    """SSE endpoint connects and sends an initial ping event."""
    from agent_lens.server import create_app
    from agent_lens.store import Store

    db_path = tmp_path / "sse_test.db"
    store = Store(path=db_path)
    csrf = "sse-test-csrf"
    app = create_app(store=store, csrf_token=csrf)

    # max_events=1 tells the server to close after the initial ping (test mode)
    with TestClient(app) as client:
        response = client.get("/events/stream", params={"max_events": 1})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        lines = response.text.splitlines()
        data_lines = [line for line in lines if line.startswith("data:")]
        assert data_lines, "No data lines in SSE response"
        first_event = json.loads(data_lines[0][5:].strip())
        assert first_event.get("type") == "ping"

    store.close()
