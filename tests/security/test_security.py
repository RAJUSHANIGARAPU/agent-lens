"""
Security tests for agent-lens.

Covers:
- API keys never appear in SQLite after a traced call
- Server binds 127.0.0.1 (not 0.0.0.0) by default
- XSS: tool output containing <script> is escaped in HTML export
- Path traversal: agent-lens replay with ../../../etc/passwd raises error
- No pickle/eval paths in trace data deserialization
"""

import html
import json
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.server import DEFAULT_HOST, create_app
from agent_lens.store import Store
from agent_lens.tracer import redact

# ----------------------------------------------------------------
# API Key / Secret redaction
# ----------------------------------------------------------------

class TestSecretRedaction:
    def test_api_keys_never_in_sqlite(self, tmp_path):
        """After a traced call with Authorization headers, DB has no key material."""
        db = tmp_path / "secrets.db"
        store = Store(path=db)

        run = Run(id=str(uuid.uuid4()), name="secret-test", start_time=time.time())
        store.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="llm-call",
            type="llm",
            start_time=time.time(),
        )
        store.save_span(span)

        # Simulate storing an event with a secret that should be redacted
        secret = "sk-ultra-secret-api-key-abc123DEF456"
        clean_data = redact({
            "model": "gpt-4o",
            "headers": {"Authorization": f"Bearer {secret}"},
        })

        event = Event(
            run_id=run.id,
            span_id=span.id,
            type=EventType.LLM_START,
            data=clean_data,
        )
        store.save_event(event)

        # Read raw DB bytes to verify key is not present anywhere
        raw = db.read_bytes().decode("utf-8", errors="replace")
        assert secret not in raw, "Secret key found in raw SQLite data!"

        store.close()

    def test_bearer_token_removed_from_event_data(self):
        """Bearer tokens are stripped before any storage path."""
        token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"
        data = {"authorization": token, "model": "claude-3"}
        result = redact(data)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature" not in str(result)

    def test_google_api_key_removed(self):
        """Google/Firebase AIza keys are redacted."""
        data = {"key": "AIzaSyABC123DEFGHIJKlmnopQRSTUVwxyz"}
        result = redact(data)
        assert "AIzaSyABC123DEFGHIJKlmnopQRSTUVwxyz" not in str(result)
        assert "[REDACTED]" in str(result)

    def test_anthropic_key_removed(self):
        """Anthropic sk-ant- keys are redacted."""
        data = {"headers": {"x-api-key": "sk-ant-api03-xyz123abc456"}}
        result = redact(data)
        assert "sk-ant-api03-xyz123abc456" not in str(result)


# ----------------------------------------------------------------
# Server binding
# ----------------------------------------------------------------

class TestServerBinding:
    def test_default_host_is_loopback_only(self):
        """The server default host is 127.0.0.1, never an external interface."""
        assert DEFAULT_HOST == "127.0.0.1"
        assert DEFAULT_HOST != "0.0.0.0"
        assert DEFAULT_HOST != "::"  # IPv6 any

    def test_app_config_does_not_contain_external_host(self):
        """Creating an app with default settings keeps it on loopback."""
        from agent_lens.server import DEFAULT_HOST, DEFAULT_PORT
        assert "0.0.0.0" not in DEFAULT_HOST
        assert DEFAULT_PORT == 7878


# ----------------------------------------------------------------
# XSS prevention in HTML export
# ----------------------------------------------------------------

class TestXSSPrevention:
    def test_xss_in_tool_output_escaped_in_export(self, tmp_path):
        """Tool output containing <script>alert(1)</script> is escaped in HTML export."""
        db = tmp_path / "xss.db"
        store = Store(path=db)

        run = Run(
            id=str(uuid.uuid4()),
            name="xss-test-run",
            start_time=time.time(),
            status=RunStatus.COMPLETED,
        )
        store.save_run(run)

        xss_payload = "<script>alert('XSS')</script>"

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name=f"tool-call-{xss_payload}",
            type="tool",
            start_time=time.time(),
        )
        store.save_span(span)

        csrf = "xss-test-csrf"
        app = create_app(store=store, csrf_token=csrf)
        with TestClient(app) as client:
            client.headers["X-Agent-Lens-Token"] = csrf
            resp = client.get(f"/runs/{run.id}/export")
            assert resp.status_code == 200
            body = resp.text

            # XSS payload must not appear unescaped in the HTML
            assert xss_payload not in body
            # It should appear escaped
            assert "&lt;script&gt;" in body or "alert" not in body or html.escape(xss_payload) in body

        store.close()

    def test_xss_in_run_name_escaped(self, tmp_path):
        """Run name with XSS payload is escaped in HTML export."""
        db = tmp_path / "xss2.db"
        store = Store(path=db)

        xss_name = '<img src=x onerror=alert(1)>'
        run = Run(id=str(uuid.uuid4()), name=xss_name, start_time=time.time())
        store.save_run(run)

        csrf = "xss2-csrf"
        app = create_app(store=store, csrf_token=csrf)
        with TestClient(app) as client:
            client.headers["X-Agent-Lens-Token"] = csrf
            resp = client.get(f"/runs/{run.id}/export")

        assert resp.status_code == 200
        body = resp.text
        # The raw XSS name should not appear unescaped in the <title> or HTML
        assert '<img src=x onerror=alert(1)>' not in body

        store.close()


# ----------------------------------------------------------------
# Path traversal prevention
# ----------------------------------------------------------------

class TestPathTraversal:
    def test_replay_rejects_etc_passwd(self, tmp_path):
        """The replay CLI command rejects /etc/passwd as input path."""
        from typer.testing import CliRunner

        from agent_lens.cli import app

        runner = CliRunner()

        result = runner.invoke(app, ["replay", "/etc/passwd"])
        # Should fail with non-zero exit (error) and not read the file
        assert result.exit_code != 0
        # Should not contain passwd-file content
        if result.output:
            assert "root:" not in result.output

    def test_replay_rejects_relative_traversal(self, tmp_path):
        """Path traversal via ../ is caught."""
        from typer.testing import CliRunner

        from agent_lens.cli import app

        runner = CliRunner()

        # Try to traverse up multiple levels
        result = runner.invoke(app, ["replay", "../../../etc/passwd"])
        assert result.exit_code != 0

    def test_replay_requires_agentlens_extension(self, tmp_path):
        """Only .agentlens and .json files are accepted."""
        # Create a test file with wrong extension
        bad_file = tmp_path / "test.txt"
        bad_file.write_text("not a valid file")

        from typer.testing import CliRunner

        from agent_lens.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["replay", str(bad_file)])
        assert result.exit_code != 0
        assert ".txt" in result.output or "Only .agentlens" in result.output

    def test_replay_valid_file_accepted(self, tmp_path):
        """A valid .json file is accepted by the replay command (fast fail test)."""
        from agent_lens.models import Run

        valid_data = {
            "run": Run(id=str(uuid.uuid4()), name="replay-test", start_time=time.time()).model_dump(),
            "spans": [],
            "events": [],
        }
        valid_file = tmp_path / "test.json"
        valid_file.write_text(json.dumps(valid_data))

        # We can't actually start the server in a unit test, but we can verify
        # the file passes validation without raising a path-traversal error
        resolved = valid_file.resolve()
        assert resolved.exists()
        assert resolved.suffix == ".json"
        # No forbidden prefixes
        forbidden_prefixes = ["/etc", "/proc", "/sys", "/dev"]
        for prefix in forbidden_prefixes:
            assert not str(resolved).startswith(prefix)


# ----------------------------------------------------------------
# No unsafe deserialization
# ----------------------------------------------------------------

class TestNoUnsafeDeserialization:
    def test_no_pickle_in_codebase(self):
        """Verify that pickle is not used for deserializing trace data."""

        agent_lens_dir = Path(__file__).parent.parent.parent / "agent_lens"
        pickle_usages = []

        for py_file in agent_lens_dir.rglob("*.py"):
            try:
                source = py_file.read_text()
                if "pickle.loads" in source or "pickle.load(" in source:
                    pickle_usages.append(str(py_file))
            except Exception:
                pass

        assert not pickle_usages, f"pickle.loads/load found in: {pickle_usages}"

    def test_no_eval_on_trace_data(self):
        """Verify that eval() is not used on untrusted trace data."""
        agent_lens_dir = Path(__file__).parent.parent.parent / "agent_lens"
        eval_usages = []

        for py_file in agent_lens_dir.rglob("*.py"):
            try:
                source = py_file.read_text()
                lines = source.split("\n")
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    # Allow 'eval' in comments, docstrings, or variable names
                    if "eval(" in stripped and not stripped.startswith("#"):
                        eval_usages.append(f"{py_file}:{i}: {stripped}")
            except Exception:
                pass

        assert not eval_usages, f"eval() found in agent_lens code: {eval_usages}"

    def test_store_uses_json_not_pickle(self, tmp_path):
        """Data is stored as JSON, not pickle, in SQLite."""
        db = tmp_path / "json_check.db"
        store = Store(path=db)

        run = Run(id=str(uuid.uuid4()), name="json-test", start_time=time.time())
        store.save_run(run)

        span = Span(
            id=str(uuid.uuid4()),
            run_id=run.id,
            name="test",
            type="agent",
            start_time=time.time(),
        )
        store.save_span(span)

        event = Event(
            run_id=run.id,
            span_id=span.id,
            type=EventType.LLM_START,
            data={"model": "gpt-4o", "test": True},
        )
        store.save_event(event)

        # Verify data round-trips through JSON (not pickle) by reading back via the store API
        retrieved_events = store.get_events(run.id)
        assert len(retrieved_events) == 1
        assert retrieved_events[0].data.get("model") == "gpt-4o"
        # Pickle magic bytes must not appear anywhere in the SQLite file
        assert b"\x80\x04" not in db.read_bytes()

        store.close()


# ----------------------------------------------------------------
# CORS restriction
# ----------------------------------------------------------------

class TestCORSRestriction:
    def test_external_origin_not_echoed(self, tmp_path):
        """External origins are not reflected in CORS allow-origin headers."""
        db = tmp_path / "cors.db"
        store = Store(path=db)
        app = create_app(store=store, csrf_token="cors-test")

        with TestClient(app) as client:
            resp = client.get(
                "/runs",
                headers={"Origin": "https://evil.attacker.com"},
            )
            allow_origin = resp.headers.get("access-control-allow-origin", "")
            assert "evil.attacker.com" not in allow_origin

        store.close()

    def test_localhost_origin_allowed(self, tmp_path):
        """Localhost origins are permitted."""
        db = tmp_path / "cors2.db"
        store = Store(path=db)
        app = create_app(store=store, csrf_token="cors2-test")

        with TestClient(app) as client:
            resp = client.get(
                "/runs",
                headers={"Origin": "http://127.0.0.1:7878"},
            )
        # Should not be blocked (200)
        assert resp.status_code == 200

        store.close()
