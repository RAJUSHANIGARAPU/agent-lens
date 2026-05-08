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

    def test_fork_with_expected_output_stored(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="expected-run", start_time=time.time())
        store.save_run(run)
        span = Span(id=str(uuid.uuid4()), run_id=run.id, name="span", type="llm", start_time=time.time())
        store.save_span(span)

        resp = c.post(
            f"/runs/{run.id}/fork",
            json={"span_id": span.id, "notes": "Shorter prompt", "expected_output": "concise"},
        )
        assert resp.status_code == 200
        forked = store.get_run(resp.json()["new_run_id"])
        assert forked.expected_output == "concise"

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
# Lineage and Diff
# ----------------------------------------------------------------

class TestLineageAndDiff:
    def test_lineage_single_run(self, client):
        c, store, csrf = client
        run = Run(id=str(uuid.uuid4()), name="root", start_time=time.time())
        store.save_run(run)

        resp = c.get(f"/runs/{run.id}/lineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["depth"] == 1
        assert data["lineage"][0]["id"] == run.id

    def test_lineage_fork_chain(self, client):
        c, store, csrf = client
        root = Run(id=str(uuid.uuid4()), name="root", start_time=time.time())
        store.save_run(root)
        fork1 = Run(
            id=str(uuid.uuid4()), name="fork1", start_time=time.time(),
            parent_run_id=root.id, notes="hypothesis A",
        )
        store.save_run(fork1)
        fork2 = Run(
            id=str(uuid.uuid4()), name="fork2", start_time=time.time(),
            parent_run_id=fork1.id, notes="hypothesis B",
        )
        store.save_run(fork2)

        resp = c.get(f"/runs/{fork2.id}/lineage")
        assert resp.status_code == 200
        chain = resp.json()["lineage"]
        assert len(chain) == 3
        assert chain[0]["id"] == root.id      # oldest first
        assert chain[1]["notes"] == "hypothesis A"
        assert chain[2]["notes"] == "hypothesis B"

    def test_diff_two_runs(self, client):
        c, store, csrf = client
        from agent_lens.models import Event, EventType

        run_a = Run(id=str(uuid.uuid4()), name="run-a", start_time=time.time())
        run_b = Run(id=str(uuid.uuid4()), name="run-b", start_time=time.time(),
                    expected_output="concise")
        store.save_run(run_a)
        store.save_run(run_b)

        span_a = Span(id=str(uuid.uuid4()), run_id=run_a.id, name="llm", type="llm", start_time=time.time())
        span_b = Span(id=str(uuid.uuid4()), run_id=run_b.id, name="llm", type="llm", start_time=time.time())
        store.save_span(span_a)
        store.save_span(span_b)

        store.save_event(Event(run_id=run_a.id, span_id=span_a.id, type=EventType.LLM_START,
                               data={"messages": [{"role": "user", "content": "Tell me about Python"}]}))
        store.save_event(Event(run_id=run_a.id, span_id=span_a.id, type=EventType.LLM_END,
                               data={"latency_ms": 1200, "input_tokens": 10, "output_tokens": 80,
                                     "cost_usd": 0.001, "response": {"content": "Python is verbose and powerful."}}))
        store.save_event(Event(run_id=run_b.id, span_id=span_b.id, type=EventType.LLM_START,
                               data={"messages": [{"role": "user", "content": "Briefly: Python?"}]}))
        store.save_event(Event(run_id=run_b.id, span_id=span_b.id, type=EventType.LLM_END,
                               data={"latency_ms": 800, "input_tokens": 8, "output_tokens": 20,
                                     "cost_usd": 0.0005, "response": {"content": "concise and readable."}}))

        resp = c.get(f"/runs/{run_a.id}/diff/{run_b.id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["messages_diff"][0]["changed"] is True
        assert data["metrics_delta"]["latency_ms"]["delta"] == -400.0
        assert data["response_diff"]["changed"] is True

        ar = data["assertion_result"]
        assert ar is not None
        assert ar["expected_output"] == "concise"
        assert ar["passed_in_b"] is True
        assert ar["passed_in_a"] is False
        assert ar["verdict"] == "improved"

    def test_diff_not_found(self, client):
        c, store, csrf = client
        resp = c.get("/runs/nonexistent/diff/also-nonexistent")
        assert resp.status_code == 404


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
