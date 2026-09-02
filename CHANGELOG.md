# Changelog

All notable changes to agent-lens are documented here.

## [0.3.0] - 2026-09-02

### Fixed
- `agent_lens.install()` raised `AttributeError` on any environment with the
  OpenAI SDK installed. The integration patched `Completions.acreate`, which no
  openai >= 1.0 has ever defined, so the documented one-line entry point failed
  for the SDK it is primarily meant to trace. Affects 0.1.0 and 0.2.0.
- Async LLM calls were not captured for either provider. The async call sites
  are `AsyncCompletions.create` and `AsyncMessages.create`; both integrations
  looked for an `acreate` attribute that no shipped SDK exposes, so the async
  branches were unreachable and async calls were traced by nothing, silently.
- `gpt-4o-mini` calls were billed at `gpt-4o` rates. Pricing families are matched
  as substrings and `gpt-4o` is contained in `gpt-4o-mini`, so a first-match scan
  over-reported cost by 25–33x depending on the input/output token mix. Matching
  now takes the longest key, which also fixes `gpt-4-turbo` and dated snapshots
  such as `claude-3-5-sonnet-20241022`.
- `unpatch()` never restored the original SDK callables — it only cleared the
  patched flag, so a patch/unpatch/patch cycle wrapped the wrapper and emitted
  duplicate spans and events for every call.
- An Anthropic message object that raised during serialisation propagated out of
  the traced call. Both integrations now degrade to a described placeholder
  rather than breaking the application being observed.

### Added
- `tests/integrations/test_sdk_surface.py` — asserts that the attributes the
  integrations patch actually exist on the installed vendor SDKs, and that
  `install()` returns rather than raises. Coverage did not catch any of the bugs
  above: the capture layer was at 85–93% and four tests specifically exercised
  the async path, all passing, because the fake SDK was shaped around what the
  code called instead of around what the vendors ship. Every other test in the
  suite runs against stubs by design — this is the one that checks the seam.
- A `sdks` extra and a matching `sdk-surface` CI job that installs the real
  openai and anthropic packages, so the surface check runs instead of skipping.
  The default matrix stays offline and fast.
- Direct unit coverage for the payload builders and pricing resolution, taking
  `openai.py` and `anthropic.py` to 100%.

### Changed
- Async spans are named `openai.achat(...)` and `anthropic.amessages(...)`, and
  now record the same payload as their sync counterparts — the async paths
  previously dropped `kwargs`, the Anthropic system prompt, and thinking blocks.

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
