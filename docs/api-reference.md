# API Reference

## Python API

### `agent_lens.install(openai=True, anthropic=True)`

Auto-patch installed LLM SDKs. Returns a dict of `{"openai": bool, "anthropic": bool}` indicating which SDKs were successfully patched.

```python
import agent_lens
results = agent_lens.install()
# {"openai": True, "anthropic": False}  # anthropic not installed
```

---

### `@agent_lens.trace`

Decorator that wraps a function as a traced `Span`.

```python
@agent_lens.trace
def my_function(x):
    return x * 2

# With options:
@agent_lens.trace(name="custom-name", span_type="llm", run_name="my-run")
def my_function(x):
    return x * 2
```

Parameters:
- `name` (str, optional) — Span name. Defaults to `function.__qualname__`.
- `span_type` (str) — One of `"agent"`, `"llm"`, `"tool"`, `"chain"`. Default: `"agent"`.
- `run_name` (str, optional) — Name for a new run if this is the root span.

Works with both sync and async functions.

---

### `agent_lens.trace_span(name, span_type="agent", run_id=None)`

Context manager version.

```python
from agent_lens.tracer import trace_span

with trace_span("my-operation") as span:
    # span.id is available here
    result = do_work()
```

---

### `agent_lens.dashboard.start(port=7878, host="127.0.0.1", open_browser=True)`

Start the dashboard server in a background thread.

Returns: `str` — the URL (e.g., `"http://127.0.0.1:7878"`).

---

### `ControlPlane.get_instance() → ControlPlane`

Get the singleton ControlPlane.

```python
from agent_lens.control import ControlPlane
cp = ControlPlane.get_instance()
```

---

### `ControlPlane.pause(run_id: str) → None`

Pause a run. The agent's thread will block at the next `before_llm_call()` checkpoint.

---

### `ControlPlane.resume(run_id: str) → None`

Resume a paused run. The blocked agent thread unblocks.

---

### `ControlPlane.step(run_id: str, num_calls: int = 1) → None`

Resume for exactly `num_calls` LLM calls, then pause again.

---

### `ControlPlane.fork(run_id, span_id, edited_messages=None, store=None) → str`

Fork a run at the given span.

Parameters:
- `run_id` — The run to fork.
- `span_id` — The span from which to fork.
- `edited_messages` — Optional list of message dicts to inject into the forked run's first LLM call.
- `store` — Optional Store instance (uses the default store if None).

Returns: `str` — The new run ID.

---

### `ControlPlane.inject(run_id: str, tool_result: Any) → None`

Inject a synthetic result into the next `before_llm_call()` for this run, and resume the run.

---

### `Store` class

```python
from agent_lens.store import Store

store = Store(path="~/.agent-lens/runs.db")  # or custom path
store = Store(path="/tmp/test.db")           # in-memory not supported; use temp file
```

Methods:
- `save_run(run: Run) → None`
- `get_run(run_id: str) → Run | None`
- `get_runs(limit=100, offset=0) → list[Run]`
- `save_span(span: Span) → None`
- `get_spans(run_id, include_parent_spans=False) → list[Span]`
- `save_event(event: Event) → None`
- `get_events(run_id, span_id=None) → list[Event]`
- `update_run_status(run_id, status, end_time=None) → None`

---

## Data Models

### `Run`

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | UUID4 |
| `name` | str | Human-readable name |
| `start_time` | float | Unix timestamp |
| `end_time` | float \| None | Unix timestamp |
| `status` | RunStatus | RUNNING, PAUSED, COMPLETED, ERROR, FORKED |
| `metadata` | dict | User-provided metadata |
| `parent_run_id` | str \| None | Set on forked runs |
| `fork_span_id` | str \| None | The span forked from |

Properties: `.duration_ms`, `.is_fork`

---

### `Span`

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | UUID4 |
| `run_id` | str | Parent run |
| `parent_id` | str \| None | Parent span |
| `name` | str | Span name |
| `type` | str | "llm", "tool", "agent", "chain" |
| `start_time` | float | Unix timestamp |
| `end_time` | float \| None | Unix timestamp |
| `status` | str | "ok", "error", "paused" |

Properties: `.duration_ms`

---

### `Event`

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | UUID4 |
| `run_id` | str | Parent run |
| `span_id` | str | Owning span |
| `type` | EventType | LLM_START, LLM_END, TOOL_START, etc. |
| `timestamp` | float | Unix timestamp |
| `data` | dict | Event payload (model, messages, response) |
| `metadata` | dict | Framework metadata |

---

## HTTP API

All mutating endpoints require the `X-Agent-Lens-Token` header. The token is printed to stdout on server start.

Base URL: `http://127.0.0.1:7878`

### `GET /runs`
List all runs (ordered by start_time descending).

Response: `[Run, ...]`

### `GET /runs/{run_id}`
Get a single run with its spans.

Response: `{...run fields, "spans": [Span, ...]}`

### `GET /runs/{run_id}/spans`
Get the span tree for a run.

Response: `[Span, ...]` (tree structure with `children` arrays)

### `GET /runs/{run_id}/export`
Export run as self-contained HTML.

Response: `text/html`

### `GET /search`
Full-text search over run records — name, notes, expected-output assertion, prompt messages, response text, and chain-of-thought. Backed by SQLite FTS5 (bm25 ranking), with a substring fallback when FTS5 is unavailable.

Query parameters:
- `q` (str, required) — search terms; multiple terms are ANDed
- `status` (str) — restrict to runs with this status
- `limit` (int, default 50), `offset` (int, default 0)

Response:
```json
{
  "query": "retry backoff",
  "count": 1,
  "results": [
    {
      "run_id": "...", "name": "...", "status": "completed",
      "score": -1.87, "snippet": "…exponential [backoff]…",
      "notes": "...", "expected_output": "concise",
      "is_fork": true, "assertion_passed": true
    }
  ]
}
```
`assertion_passed` is present only when the run has an `expected_output`. Returns `400` if `q` is empty.

### `GET /runs/{run_id}/export/ctx`
Export a single run for indexing in an external search tool.

Query parameters:
- `format` (str, default `ndjson`) — `ndjson` (provider-neutral document with structured outcome labels) or `codex` (Codex-format session records ingestible by `ctx import --path`)

Response: `application/x-ndjson`. `ndjson` emits one document; `codex` emits one line per session record. Returns `400` for an unknown format, `404` if the run is missing.

The `ndjson` document:
```json
{
  "id": "<run_id>", "source": "agent-lens", "title": "<name>",
  "text": "<flattened messages, response, thinking, notes>",
  "metadata": {
    "run_id": "...", "status": "completed", "parent_run_id": null,
    "is_fork": false, "notes": "...", "expected_output": "concise",
    "assertion_passed": true, "started_at": "2026-07-01T10:00:00Z",
    "ended_at": "...", "start_time": 1751362800.0, "duration_ms": 4210,
    "total_tokens": 1234, "cost_usd": 0.021, "num_events": 12, "url": "/runs/..."
  }
}
```

### `GET /export/ctx`
Stream the whole run corpus as NDJSON (one line per run), for bulk indexing.

Query parameters:
- `format` (str, default `ndjson`) — `ndjson` or `codex`
- `status` (str) — restrict to runs with this status
- `limit` (int) — cap the number of runs exported

Response: `application/x-ndjson` (streamed).

### `POST /runs/{run_id}/pause`
Pause a run.

Response: `{"status": "paused", "run_id": "..."}`

### `POST /runs/{run_id}/resume`
Resume a paused run.

Response: `{"status": "running", "run_id": "..."}`

### `POST /runs/{run_id}/step`
Step one LLM call.

Response: `{"status": "stepping", "run_id": "..."}`

### `POST /runs/{run_id}/fork`
Fork a run.

Body:
```json
{
  "span_id": "uuid",
  "edited_messages": [{"role": "user", "content": "..."}]
}
```

Response: `{"status": "forked", "run_id": "...", "new_run_id": "..."}`

### `POST /runs/{run_id}/inject`
Inject a synthetic tool result.

Body:
```json
{"tool_result": {"output": "mocked response"}}
```

Response: `{"status": "injected", "run_id": "..."}`

### `POST /runs/{run_id}/note`
Add or update a developer note on any run (not just forks).

Body:
```json
{"notes": "why this run matters"}
```

Response: `{"status": "ok", "run_id": "..."}`

### `GET /runs/{run_id}/lineage`
Return the full fork ancestry chain for a run, oldest first.

Response: `{"run_id": "...", "lineage": [{...run summary, "notes": "...", "depth": 0}, ...], "depth": N}`

### `GET /runs/{run_id}/diff/{other_run_id}`
Compare two runs structurally: message diff, response diff, metrics delta, and — when either run has an `expected_output` — an assertion result with a comparative verdict (`improved` / `regressed` / `both_pass` / `neither_pass`).

Response: `{"messages_diff": [...], "response_diff": {...}, "metrics_delta": {...}, "thinking_blocks": {...}, "assertion_result": {...}}`

### `GET /events/stream`
Server-Sent Events stream. Emits all events in real time.

Format: `data: {"type": "span_start", ...}\n\n`

Event types: `ping`, `keepalive`, `run_start`, `run_end`, `span_start`, `span_end`, `event`

### `GET /info`
Server version and host info.

Response: `{"version": "0.1.0", "host": "127.0.0.1"}`

---

## CLI

```
agent-lens dashboard [--port PORT] [--host HOST] [--no-browser]
agent-lens replay <file.agentlens>  [--port PORT]
agent-lens export <run_id>          [--output FILE]
agent-lens export-ctx               [--output FILE] [--format ndjson|codex] [--status STATUS]
agent-lens version
```

### `agent-lens dashboard`
Start the dashboard server.

Options:
- `--port` (int) — Port to bind, default 7878
- `--host` (str) — Host to bind, default 127.0.0.1
- `--no-browser` — Don't open browser automatically

### `agent-lens replay <file>`
Load a `.agentlens` or `.json` export file into a temporary dashboard.

### `agent-lens export <run_id>`
Export a run as a self-contained HTML file.

Options:
- `--output` (str) — Output file path, default `run-<id>.html`

### `agent-lens export-ctx`
Export all runs as NDJSON for indexing in an external search tool (e.g. `ctx`).

Options:
- `--output` (str) — Output file path, default `agent-lens-corpus.{ctx,codex}.jsonl`
- `--format` (str) — `ndjson` (provider-neutral, default) or `codex` (ingestible via `ctx import --path`)
- `--status` (str) — Only export runs with this status

### `agent-lens version`
Print version string.
