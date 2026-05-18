# Changelog

All notable changes to agent-lens are documented here.

## [0.2.0] - 2026-05-18

### Added
- LlamaIndex callback handler (`AgentLensLlamaIndexHandler`) — trace any LlamaIndex query engine or chat engine with zero code changes
- GitHub issue templates for bug reports and feature requests
- Colab quickstart notebook — full hypothesis → fork → diff workflow in 5 minutes, no API key required
- LangChain hypothesis testing example (`examples/08_langchain_hypothesis.py`)
- Automated demo GIF generation via GitHub Actions (VHS)
- Good-first-issues table in CONTRIBUTING.md

### Fixed
- VHS CI action: pre-install ffmpeg and ttyd to avoid install failures on ubuntu-latest

## [0.1.0] - 2026-05-02

### Added
- Core tracer with OpenAI SDK auto-patch (`agent_lens.install()`)
- Anthropic SDK integration with extended thinking capture
- LangChain callback handler (`AgentLensCallbackHandler`)
- FastAPI dashboard server at `localhost:7878`
- SQLite store — all traces persist in `~/.agent-lens/runs.db`
- `POST /runs/{id}/fork` — fork with hypothesis notes and expected_output assertion
- `GET /runs/{a}/diff/{b}` — structural diff: messages, metrics delta, verdict
- `GET /runs/{id}/lineage` — full ancestry chain of forked runs
- `POST /runs/{id}/note` — annotate any run after the fact
- Pause / resume control plane — blocks agent at next LLM call
- Secret redaction — Bearer tokens, `sk-*`, `AIza*`, `sk-ant-*` stripped before SQLite
- Self-contained HTML export — `agent-lens export <run_id> --output trace.html`
- Real-time SSE dashboard with span tree, flame graph, message inspector
- `@agent_lens.trace` decorator for any Python function
