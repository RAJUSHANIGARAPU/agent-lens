# Architecture: agent-lens

## Module Layout

```
agent-lens/
├── pyproject.toml
├── Makefile
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── BUILD_LOG.md
│
├── agent_lens/
│   ├── __init__.py          # Public API: trace, dashboard, install
│   ├── models.py            # Data models: Event, Span, Run, enums
│   ├── tracer.py            # Core capture engine, @trace decorator
│   ├── store.py             # SQLite persistence layer
│   ├── control.py           # Pause/Resume/Fork/Step/Inject engine
│   ├── server.py            # FastAPI application and SSE
│   ├── dashboard_launcher.py# Background server + browser launcher
│   ├── cli.py               # Typer CLI entry point
│   │
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── openai.py        # OpenAI SDK monkey-patch
│   │   ├── anthropic.py     # Anthropic SDK monkey-patch
│   │   └── langchain.py     # LangChain callback handler
│   │
│   └── dashboard/
│       ├── index.html       # Single-file dashboard shell
│       ├── app.js           # Vanilla JS application
│       └── style.css        # Dark-mode CSS
│
├── tests/
│   ├── test_tracer.py
│   ├── test_server.py
│   ├── test_control.py
│   ├── integration/
│   │   ├── test_e2e_openai.py
│   │   ├── test_e2e_anthropic.py
│   │   ├── test_pause_fork.py
│   │   ├── test_concurrency.py
│   │   └── test_overhead.py
│   └── security/
│       └── test_security.py
│
├── examples/
│   ├── 01_openai_quickstart.py
│   ├── 02_anthropic_tools.py
│   ├── 03_langchain_chain.py
│   └── 04_pause_and_fork.py
│
└── docs/
    ├── PROBLEM.md
    ├── ARCHITECTURE.md
    ├── SECURITY.md
    ├── FIRST_RUN.md
    ├── getting-started.md
    ├── pause-and-fork.md
    ├── api-reference.md
    ├── demo-storyboard.md
    └── LAUNCH.md
```

---

## Data Model

### EventType (enum)
```python
class EventType(str, Enum):
    LLM_START    = "llm_start"
    LLM_END      = "llm_end"
    TOOL_START   = "tool_start"
    TOOL_END     = "tool_end"
    AGENT_START  = "agent_start"
    AGENT_END    = "agent_end"
    ERROR        = "error"
```

### RunStatus (enum)
```python
class RunStatus(str, Enum):
    RUNNING    = "running"
    PAUSED     = "paused"
    COMPLETED  = "completed"
    ERROR      = "error"
    FORKED     = "forked"
```

### Event
| Field           | Type            | Description                               |
|-----------------|-----------------|-------------------------------------------|
| id              | str (UUID4)     | Unique event identifier                   |
| run_id          | str (UUID4)     | Parent run identifier                     |
| span_id         | str (UUID4)     | Owning span identifier                    |
| parent_span_id  | str \| None     | Parent span (for nested calls)            |
| type            | EventType       | Event type enum                           |
| timestamp       | float           | Unix timestamp (time.time())              |
| data            | dict            | Event payload (model, messages, response) |
| metadata        | dict            | Framework-specific metadata               |

### Span
| Field      | Type          | Description                                |
|------------|---------------|--------------------------------------------|
| id         | str (UUID4)   | Unique span identifier                     |
| run_id     | str (UUID4)   | Parent run                                 |
| parent_id  | str \| None   | Parent span (None for root)                |
| name       | str           | Human-readable span name                  |
| type       | str           | "llm", "tool", "agent", "chain"            |
| start_time | float         | Unix timestamp                             |
| end_time   | float \| None | None while span is open                    |
| events     | list[Event]   | Events belonging to this span              |
| children   | list[Span]    | Child spans (resolved at query time)       |
| status     | str           | "ok", "error", "paused"                    |

### Run
| Field         | Type          | Description                              |
|---------------|---------------|------------------------------------------|
| id            | str (UUID4)   | Unique run identifier                    |
| name          | str           | Human-readable run name                  |
| start_time    | float         | Unix timestamp                           |
| end_time      | float \| None | None while run is open                   |
| status        | RunStatus     | Current run status                       |
| root_span     | Span \| None  | Root span (resolved at query time)       |
| metadata      | dict          | User-provided metadata                   |
| parent_run_id | str \| None   | Set when this run is a fork              |
| fork_span_id  | str \| None   | The span from which this run was forked  |

---

## SQLite Schema

```sql
-- Database location: ~/.agent-lens/runs.db

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    start_time  REAL    NOT NULL,
    end_time    REAL,
    status      TEXT    NOT NULL DEFAULT 'running',
    parent_run_id TEXT,
    fork_span_id  TEXT,
    metadata    TEXT    DEFAULT '{}'  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_runs_start_time ON runs(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);

CREATE TABLE IF NOT EXISTS spans (
    id          TEXT    PRIMARY KEY,
    run_id      TEXT    NOT NULL REFERENCES runs(id),
    parent_id   TEXT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL,
    start_time  REAL    NOT NULL,
    end_time    REAL,
    status      TEXT    NOT NULL DEFAULT 'ok',
    metadata    TEXT    DEFAULT '{}'  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_spans_run_id    ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent_id ON spans(parent_id);
CREATE INDEX IF NOT EXISTS idx_spans_start_time ON spans(start_time);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT    PRIMARY KEY,
    run_id         TEXT    NOT NULL REFERENCES runs(id),
    span_id        TEXT    NOT NULL REFERENCES spans(id),
    parent_span_id TEXT,
    type           TEXT    NOT NULL,
    timestamp      REAL    NOT NULL,
    data           TEXT    DEFAULT '{}'  -- JSON
    metadata       TEXT    DEFAULT '{}'  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_events_run_id    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_span_id   ON events(span_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
```

---

## Public API Surface

```python
# agent_lens/__init__.py

# Decorator
@agent_lens.trace
def my_function(...): ...

# Auto-patching
agent_lens.install()  # patches OpenAI + Anthropic SDKs

# Dashboard
agent_lens.dashboard.start(port=7878, host="127.0.0.1", open_browser=True)

# ControlPlane (programmatic)
from agent_lens.control import ControlPlane
cp = ControlPlane.get_instance()
cp.pause(run_id)
cp.resume(run_id)
cp.step(run_id)
new_run_id = cp.fork(run_id, span_id, edited_messages=[...])
cp.inject(run_id, tool_result={"output": "..."})

# Store (direct access)
from agent_lens.store import Store
store = Store()
runs = await store.get_runs()
run = await store.get_run(run_id)
spans = await store.get_spans(run_id)
events = await store.get_events(run_id)
```

---

## Pause / Resume / Fork Flow

```
┌──────────────┐          ┌────────────────┐         ┌──────────────┐
│  User Code   │          │  agent-lens    │         │  Dashboard   │
│  (Agent)     │          │  (Tracer +     │         │  (Browser)   │
│              │          │  ControlPlane) │         │              │
└──────┬───────┘          └───────┬────────┘         └──────┬───────┘
       │                          │                          │
       │  @trace enters span      │                          │
       ├─────────────────────────►│                          │
       │                          │  save_span(start)        │
       │                          ├──────────────────────────┤ SSE: span_start
       │                          │                          │
       │  [LLM call about to      │                          │
       │   happen]                │                          │
       │  should_pause(run_id)?   │                          │
       ├─────────────────────────►│                          │
       │   returns True           │◄─────────────────────────┤ POST /pause
       │◄─────────────────────────│                          │
       │                          │  emit SSE: paused        │
       │  wait_for_resume()       │ ─────────────────────────►
       │  [BLOCKED]               │                          │  show pause UI
       │                          │                          │  user edits msgs
       │                          │◄─────────────────────────┤ POST /fork
       │                          │                          │  {edited_messages}
       │                          │  create new Run          │
       │                          │  copy spans up to        │
       │                          │  fork_span_id            │
       │                          │  new_run_id              │
       │                          ├──────────────────────────► SSE: fork_created
       │                          │                          │
       │  resume original         │◄─────────────────────────┤ POST /resume
       │  [UNBLOCKED]             │                          │
       │◄─────────────────────────│                          │
       │  continue execution      │                          │
       │                          │                          │
       │  @trace exits span       │                          │
       ├─────────────────────────►│  save_span(end)          │
       │                          ├──────────────────────────► SSE: span_end
       │                          │                          │
```

**Fork semantics**: A forked Run stores `parent_run_id` and `fork_span_id`. When the API resolves spans for the forked run, it queries spans from the original run up to and including `fork_span_id`, then returns spans from the forked run for all events after. This avoids data duplication. The new run starts execution with the edited messages injected via `ControlPlane.inject()`.

---

## Threading Model

```
Main Thread (user's agent code)
│
├── TraceContext (contextvars.ContextVar)
│   └── current_run_id, current_span_id, span_stack
│
├── ControlPlane (singleton)
│   └── _pause_events: Dict[run_id, threading.Event]
│   └── _step_counts: Dict[run_id, int]
│
├── Store (singleton, thread-safe via threading.Lock)
│   └── SQLite connection pool
│   └── _write_lock: threading.Lock
│
├── EventBus (singleton)
│   └── _subscribers: List[asyncio.Queue]
│   └── _loop: asyncio.AbstractEventLoop (background thread)
│
└── Server Thread (uvicorn, background daemon thread)
    └── FastAPI app
    └── SSE subscribers
```

**Key properties**:
- `contextvars.ContextVar` ensures each thread/coroutine has its own trace context. No cross-contamination between concurrent agent runs.
- The `Store` uses a single write lock to serialize SQLite writes. Reads are concurrent (SQLite WAL mode).
- The `EventBus` bridges the synchronous tracer (in user threads) to the async SSE server (in the uvicorn thread). Events are put onto thread-safe queues and drained by the async server loop.
- `ControlPlane.pause()` sets a `threading.Event` per `run_id`. The tracer's `should_pause()` checks this before every LLM call. `wait_for_resume()` calls `event.wait()`, blocking the user's thread. This is intentional: the agent's thread is blocked, not killed.

---

## Security Boundaries

1. **Network**: Server binds `127.0.0.1` only. No external connections possible without explicit override.
2. **CORS**: Only `http://localhost:*` and `http://127.0.0.1:*` origins are allowed.
3. **CSRF**: A random 32-byte token is generated at server startup and printed to stdout. API endpoints that mutate state (pause, resume, fork, inject) require this token in the `X-Agent-Lens-Token` header.
4. **Secret redaction**: All event data is passed through a redaction pipeline before storage. The regex `(Bearer\s+\S+|sk-[A-Za-z0-9-_]+|AIza[A-Za-z0-9_-]+|[Aa]uth[a-z]*[:=]\s*\S+)` strips API keys from all string fields recursively.
5. **Data locality**: All data stays in `~/.agent-lens/runs.db`. No telemetry, no callbacks, no network egress.
6. **Input validation**: FastAPI's Pydantic models validate all incoming request bodies. No `eval()`, `pickle`, or `exec()` paths in the codebase.
7. **HTML export**: Tool outputs and LLM responses are HTML-escaped before being embedded in exported files to prevent XSS.
8. **Path traversal**: The `replay` CLI command validates that the input file path resolves within expected directories before opening.
