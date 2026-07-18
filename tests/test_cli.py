"""
Tests for agent_lens.cli — the Typer CLI (previously 0% covered).

Drives every command through Typer's CliRunner. The two commands that would
otherwise block on a dashboard server loop (`dashboard`, `replay`) are exercised
by stubbing the launcher and making the keep-alive `time.sleep` raise
KeyboardInterrupt, so the command reaches its graceful-shutdown path and exits.
Store-backed commands (`export`, `export-ctx`, `search`) run against the
per-test default store seeded directly.
"""

import json
import sys
import time
import uuid

import pytest
from typer.testing import CliRunner

from agent_lens.cli import app
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import get_default_store

runner = CliRunner()


def _raise_keyboard_interrupt(*_args, **_kwargs):
    raise KeyboardInterrupt


def _combined(result) -> str:
    """stdout + stderr, across click versions (8.2+ captures stderr separately)."""
    text = result.output or ""
    try:
        if result.stderr:
            text += result.stderr
    except (ValueError, RuntimeError):
        pass
    return text


def _seed_run(store, *, name="run", status=RunStatus.COMPLETED, message="hello there",
              response="general answer", notes=None, expected_output=None):
    run = Run(id=str(uuid.uuid4()), name=name, start_time=time.time(),
              status=status, notes=notes, expected_output=expected_output)
    store.save_run(run)
    span = Span(id=str(uuid.uuid4()), run_id=run.id, name="llm", type="llm", start_time=time.time())
    store.save_span(span)
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_START,
                           data={"messages": [{"role": "user", "content": message}]}))
    store.save_event(Event(run_id=run.id, span_id=span.id, type=EventType.LLM_END,
                           data={"response": {"content": response}, "input_tokens": 10,
                                 "output_tokens": 5, "cost_usd": 0.001}))
    store.reindex_run(run.id)
    return run


# ---- version -----------------------------------------------------

def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "agent-lens" in result.output


# ---- dashboard ---------------------------------------------------

def test_dashboard_starts_then_exits_on_interrupt(monkeypatch):
    from agent_lens import dashboard_launcher

    captured = {}
    monkeypatch.setattr(dashboard_launcher, "start", lambda **kw: captured.update(kw))
    monkeypatch.setattr(time, "sleep", _raise_keyboard_interrupt)

    result = runner.invoke(app, ["dashboard", "--port", "9191", "--host", "0.0.0.0", "--no-browser"])
    assert result.exit_code == 0
    assert captured == {"port": 9191, "host": "0.0.0.0", "open_browser": False}
    assert "Shutting down" in result.output


# ---- replay: validation branches ---------------------------------

def test_replay_file_not_found():
    result = runner.invoke(app, ["replay", "/tmp/agent-lens-definitely-missing.agentlens"])
    assert result.exit_code == 1
    assert "File not found" in _combined(result)


@pytest.mark.skipif(sys.platform == "win32", reason="forbidden-prefix guard is Unix-only (cli.py skips it on win32)")
def test_replay_rejects_forbidden_prefix():
    # /dev is a real (non-symlink) directory on both Linux and macOS, so
    # Path.resolve() keeps it under a forbidden prefix on either platform.
    # (/etc is a symlink to /private/etc on macOS, which would dodge the check.)
    result = runner.invoke(app, ["replay", "/dev/null"])
    assert result.exit_code == 1
    assert "not allowed" in _combined(result)


def test_replay_rejects_bad_suffix(tmp_path):
    p = tmp_path / "run.txt"
    p.write_text("{}")
    result = runner.invoke(app, ["replay", str(p)])
    assert result.exit_code == 1
    assert "Only .agentlens and .json" in _combined(result)


def test_replay_rejects_non_file(tmp_path):
    d = tmp_path / "dir.json"
    d.mkdir()
    result = runner.invoke(app, ["replay", str(d)])
    assert result.exit_code == 1
    assert "Not a file" in _combined(result)


def test_replay_rejects_bad_json(tmp_path):
    p = tmp_path / "run.json"
    p.write_text("{ this is not json")
    result = runner.invoke(app, ["replay", str(p)])
    assert result.exit_code == 1
    assert "Error reading file" in _combined(result)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="replay unlinks its still-open temp SQLite file, which raises PermissionError on Windows",
)
def test_replay_loads_and_starts(tmp_path, monkeypatch):
    from agent_lens import dashboard_launcher

    monkeypatch.setattr(dashboard_launcher, "start", lambda **kw: None)
    monkeypatch.setattr(time, "sleep", _raise_keyboard_interrupt)

    run_id = str(uuid.uuid4())
    payload = {
        "run": {"id": run_id, "name": "loaded-run", "start_time": 1.0, "status": "completed"},
        "spans": [{"id": "s1", "run_id": run_id, "name": "llm", "type": "llm", "start_time": 1.0}],
        "events": [{"run_id": run_id, "span_id": "s1", "type": "llm_start", "data": {}}],
    }
    p = tmp_path / "run.json"
    p.write_text(json.dumps(payload))

    result = runner.invoke(app, ["replay", str(p)])
    assert result.exit_code == 0
    assert "Run loaded" in result.output


# ---- export ------------------------------------------------------

def test_export_run_not_found():
    result = runner.invoke(app, ["export", "no-such-run"])
    assert result.exit_code == 1
    assert "not found" in _combined(result)


def test_export_writes_html(tmp_path):
    run = _seed_run(get_default_store(), name="export-me")
    out = tmp_path / "out.html"
    result = runner.invoke(app, ["export", run.id, "-o", str(out)])
    assert result.exit_code == 0
    assert "Exported to" in result.output
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert run.id in html
    assert "agent-lens export" in html


# ---- export-ctx --------------------------------------------------

def test_export_ctx_rejects_bad_format():
    result = runner.invoke(app, ["export-ctx", "-f", "xml"])
    assert result.exit_code == 1
    assert "'ndjson' or 'codex'" in _combined(result)


def test_export_ctx_writes_corpus(tmp_path):
    store = get_default_store()
    _seed_run(store, name="one")
    _seed_run(store, name="two")
    out = tmp_path / "corpus.jsonl"
    result = runner.invoke(app, ["export-ctx", "-o", str(out)])
    assert result.exit_code == 0
    assert "Exported 2 run(s)" in result.output
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2  # ndjson: one document per run


def test_export_ctx_codex_format(tmp_path):
    _seed_run(get_default_store(), name="cx")
    out = tmp_path / "corpus.codex.jsonl"
    result = runner.invoke(app, ["export-ctx", "-f", "codex", "-o", str(out)])
    assert result.exit_code == 0
    # codex emits multiple records per run (session_meta + items)
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) > 1
    assert json.loads(lines[0])["type"] == "session_meta"


# ---- search ------------------------------------------------------

def test_search_rejects_empty_query():
    result = runner.invoke(app, ["search", "   "])
    assert result.exit_code == 1
    assert "non-empty" in _combined(result)


def test_search_reports_no_hits():
    result = runner.invoke(app, ["search", "zzz-no-such-term-anywhere"])
    assert result.exit_code == 0
    assert "No runs matched" in result.output


def test_search_reports_hits():
    run = _seed_run(get_default_store(), name="unicorn-run", message="find the unicorn please")
    result = runner.invoke(app, ["search", "unicorn"])
    assert result.exit_code == 0
    assert "unicorn-run" in result.output
    assert run.id in result.output


# ---- mcp ---------------------------------------------------------

def test_mcp_reports_missing_extra(monkeypatch):
    from agent_lens import mcp_server

    def _raise_import(*_a, **_k):
        raise ImportError("mcp not installed")

    monkeypatch.setattr(mcp_server, "main", _raise_import)
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 1
    assert "mcp" in _combined(result).lower()


def test_mcp_runs_server(monkeypatch):
    from agent_lens import mcp_server

    called = {}
    monkeypatch.setattr(mcp_server, "main", lambda: called.setdefault("ran", True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0
    assert called.get("ran") is True
