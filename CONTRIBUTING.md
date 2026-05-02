# Contributing to agent-lens

Thank you for your interest in contributing! agent-lens is an early-stage open-source project and all contributions are genuinely welcome.

## Ways to Contribute

- **Bug reports** — Open an issue with steps to reproduce, expected behavior, actual behavior, and your Python version.
- **Feature requests** — Open an issue describing the use case (not just the feature). We prioritize based on "how many developers hit this problem."
- **Code** — See below.
- **Documentation** — Fix typos, add examples, clarify confusing sections.
- **Testing** — Add test cases, especially for edge cases and concurrency.

## Good First Issues

If you're new to the project, look for issues tagged `good first issue`. Some concrete ones to start with:

- Add a LlamaIndex callback handler (follow the pattern in `agent_lens/integrations/langchain.py`)
- Add async-aware pause/resume using `asyncio.Event`
- Add a `--json` flag to `agent-lens export` for JSON output instead of HTML
- Improve the timeline flame graph with zoom/pan support
- Add a "copy span ID" button to the inspector
- Write a test for the SSE reconnection logic

## Development Setup

```bash
# Clone
git clone https://github.com/RAJUSHANIGARAPU/agent-lens.git
cd agent-lens

# Install in editable mode with dev deps
pip install -e ".[dev]"

# Verify tests pass
pytest tests/ -v
```

## Running the Demo

```bash
python examples/04_pause_and_fork.py
```

## Code Style

- **Ruff** for linting: `ruff check agent_lens/ tests/`
- **Type hints** on all public functions
- **Docstrings** on all public classes and functions
- No `@Autowired` (this is Python :) — constructor injection only
- Constructor injection for dependencies (no globals in business logic except the singletons in `tracer.py`, `control.py`, `store.py`)

## Testing

All new code needs tests. The test structure:

```
tests/
  test_tracer.py        — unit tests for the tracer
  test_server.py        — FastAPI app tests
  test_control.py       — ControlPlane unit tests
  integration/          — end-to-end tests
  security/             — security-focused tests
```

Run the full test suite:
```bash
pytest tests/ --cov=agent_lens -v
```

Run a specific file:
```bash
pytest tests/test_tracer.py -v
```

## Pull Request Process

1. Fork the repo and create a feature branch: `git checkout -b feat/my-feature`
2. Write tests first (TDD encouraged but not required)
3. Implement the feature
4. Ensure all tests pass: `pytest tests/`
5. Lint: `ruff check agent_lens/ tests/`
6. Open a PR with a clear description of what changed and why

## Commit Message Format

```
type(scope): description

Types: feat, fix, chore, refactor, test, docs
Scope: tracer, store, control, server, dashboard, cli, integrations

Examples:
  feat(control): add async pause/resume using asyncio.Event
  fix(tracer): prevent context leak between async tasks
  test(security): add path traversal test for replay command
  docs(api): add WebSocket events documentation
```

## Architecture

Before making significant changes, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Key design constraints:

- The tracer overhead must stay < 5ms per call
- SQLite is the only storage backend (no PostgreSQL, no Redis)
- The server must bind to 127.0.0.1 by default
- No data must ever leave the machine
- No `eval()`, `exec()`, or `pickle` in any data deserialization path

## Questions?

Open a GitHub Discussion or email rajub4u927@gmail.com.
