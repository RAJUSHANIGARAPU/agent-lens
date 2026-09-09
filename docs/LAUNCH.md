## Show HN Title

```
Show HN: agent-lens – pause a running LLM agent, fork it, get a verdict
```

## Post Body (Show HN text)

agent-lens pauses a running LLM agent mid-call, lets you edit the prompt or a tool response, forks the run into a second branch, and resumes the original — so you end up with two comparable runs and a script-checkable verdict of `improved`, `regressed`, `both_pass`, or `neither_pass`.

You don't need a live agent to see the mechanic in under a minute: `pip install agentlens-tracer`, run one bundled Python script, and it writes two sample runs, forks, prints deltas and a verdict against a throwaway local database — no API key, no account, no Docker. Those two runs are fixtures rather than real LLM calls, so the numbers it prints are made up; what's real is the store, the diff endpoint and the verdict it puts them through.

The name is not unique. An unrelated AgentLens was posted here in March 2026 — different project, no connection. This one is `agent-lens`, `agentlens-tracer` on PyPI.

Before you install it, the parts that aren't there yet: forked runs are not auto-relaunched, you restart the forked agent yourself; it's a local, single-user tool with no shared dashboard, so a team that already runs a hosted multi-user eval pipeline is better served by something like Arize Phoenix; there's no hosted offering at all — it's local-first and SQLite by design, so if you want zero local infrastructure Langfuse or LangSmith fit that better; it's built for development and debugging, not production monitoring; and the database needs a real file path, there's no in-memory mode.

Beyond the pause/fork/verdict loop, agent-lens also gives you:

- Zero infrastructure: SQLite on disk, no Docker, no cloud, no tool API keys.
- Real-time dashboard: span tree, flame graph timeline, message inspector, live over SSE.
- Any framework: OpenAI, Anthropic, LangChain via callback, or any Python function via `@trace`.
- Anthropic extended thinking captured: `thinking_blocks` flow into the trace alongside the response.
- Self-contained HTML export: `agent-lens export <run_id>` shares one file with a colleague, no login, no dashboard needed to view it.
- Secret redaction: Bearer tokens, `sk-*`, `AIza*`, `sk-ant-*` stripped before they hit SQLite.

agent-lens is a framework-agnostic, local-first debugger for LLM agents — pause, fork, and get a numeric verdict on which run actually worked. It's MIT-licensed, needs Python 3.10+, installs with `pip install agentlens-tracer`, and the source is at https://github.com/RAJUSHANIGARAPU/agent-lens. Feedback and bug reports welcome.

## Longer Writeup

agent-lens pauses a running LLM agent mid-call, lets you edit the prompt or a tool response, forks the run into a second branch, and resumes the original — so you end up with two comparable runs and a script-checkable verdict of `improved`, `regressed`, `both_pass`, or `neither_pass`. That loop — pause, fork, verdict — is the entire pitch; everything else below is detail.

Most "compare two agent runs" tooling operates on runs that have already finished, or on checkpoints written after the fact. agent-lens keeps both runs comparable while the original process is still alive, and turns the comparison into a verdict instead of an impression.

### The 60-second quickstart

You don't need a running agent to see the mechanic. `pip install agentlens-tracer`, run one bundled Python script, and it records two runs, forks, prints deltas and a verdict against a throwaway local database — no API key, no account, no Docker. The script saves a completed baseline run, saves a second run marked as a fork of the first with a stated hypothesis, and then calls the same diff endpoint the dashboard uses. It's a fixture, not a live agent call — the point is to show the fork/diff/verdict machinery working end to end before you point it at anything real. For a real agent, the pause happens live: agent-lens blocks the process at its next LLM call, lets you edit messages from the dashboard, and only then forks and resumes.

### What else you get

- Zero infrastructure: SQLite on disk, no Docker, no cloud, no tool API keys.
- Real-time dashboard: span tree, flame graph timeline, message inspector, live over SSE.
- Any framework: OpenAI, Anthropic, LangChain via callback, or any Python function via `@trace`.
- Anthropic extended thinking captured: `thinking_blocks` flow into the trace alongside the response.
- Self-contained HTML export: `agent-lens export <run_id>` shares one file with a colleague, no login, no dashboard needed to view it.
- Secret redaction: Bearer tokens, `sk-*`, `AIza*`, `sk-ant-*` stripped before they hit SQLite.

### What it doesn't do yet, and who should skip it

Forked runs are not auto-relaunched — forking creates a new Run record, but you restart the forked agent process yourself. It's a local, single-user tool: there's no shared or team dashboard, so if your team already runs a hosted, multi-user, dataset-driven eval pipeline against completed executions and wants a shared team view, Arize Phoenix experiments fit that directly. There's no hosted offering at all, by design: everything is local-first and SQLite, so if you want a hosted SaaS trace-analysis product with zero local infrastructure, Langfuse or LangSmith fit that better. It's built for development and debugging, not production monitoring — the dashboard binds to 127.0.0.1 only, and nothing about it is hardened for a production network. And there's no in-memory database mode — the store always resolves to a real file path, either the default `~/.agent-lens/runs.db` or one you pass in yourself.

agent-lens is a framework-agnostic, local-first debugger for LLM agents — pause, fork, and get a numeric verdict on which run actually worked. It's MIT-licensed, needs Python 3.10+, installs with `pip install agentlens-tracer`, and the source is at https://github.com/RAJUSHANIGARAPU/agent-lens.

## Notes (citations — do not post this section)

- FLAG FOR HUMAN: an unrelated project named "AgentLens" was Show HN'd March 2026 (2 pts, overlapping trace-comparison feature). The post body now names it in one line rather than waiting to be corrected in the thread. That disclosure is not the same as a decision: whether to keep the name, rename, or say nothing at all is still open, and the wording is a placeholder to be rewritten in the author's own voice.
- CLAIM: "those two runs are fixtures rather than real LLM calls, so the numbers it prints are made up" — SOURCE: README.md:92 and README.md:105 (`data={"latency_ms": 1847, ...}` / `820`, hand-written into the demo script's save_event calls) and README.md:55 ("**The two runs are sample data, not measurements.**"). Added because the longer writeup disclosed this and the post body did not, and the post body is the part that gets read.
- CLAIM: "agent-lens pauses a running LLM agent mid-call, lets you edit the prompt or a tool response, forks the run into a second branch, and resumes the original" — SOURCE: README.md:222-236 (pause/fork/resume diagram, "No restarts. No re-running preceding steps.")
- CLAIM: "a script-checkable verdict of `improved`, `regressed`, `both_pass`, or `neither_pass`" — SOURCE: agent_lens/compare.py:98-100 (verdict computed as improved/regressed/both_pass/neither_pass)
- CLAIM: "Most "compare two agent runs" tooling operates on runs that have already finished, or on checkpoints written after the fact." — SOURCE: docs/COMPARISON.md:9 (exact sentence)
- CLAIM: "agent-lens is a framework-agnostic, local-first debugger for LLM agents — pause, fork, and get a numeric verdict on which run actually worked." — SOURCE: docs/COMPARISON.md:3 (exact sentence; same wording, minus the lead-in, at pyproject.toml:8)
- CLAIM: "pip install agentlens-tracer" — SOURCE: pyproject.toml:6 (`name = "agentlens-tracer"`) and live `curl -s https://pypi.org/pypi/agentlens-tracer/json | python3 -c "import json,sys;print(json.load(sys.stdin)['info']['name'])"` run 2026-09-08, printed `agentlens-tracer`
- NOTE: the same curl against `['info']['version']` printed `0.3.1` on 2026-09-08 — a freshly-confirmed live value, not carried over from an earlier scouting run — but a version number is deliberately not stated in the post copy since it goes stale within a release cycle.
- CLAIM: "records two runs, forks, prints deltas and a verdict" — SOURCE: README.md:49-124 (header at 49, script at 55-117, explanation at 119-124)
- NOTE: README.md:84 and README.md:97 contain the quickstart's hardcoded fixture numbers (`latency_ms: 1847`/`820`, `total_tokens: 453`/`87`) written directly into the demo script's `data={...}` calls — not a measurement from a real LLM call. Deliberately not quoted anywhere in this draft.
- CLAIM: "the pause happens live: agent-lens blocks the process at its next LLM call, lets you edit messages from the dashboard, and only then forks and resumes" — SOURCE: README.md:222-236 (pause/fork/resume diagram) and docs/COMPARISON.md:7,17 (the pause claim under evaluation, agent-lens row)
- CLAIM: "Zero infrastructure: SQLite on disk, no Docker, no cloud, no tool API keys." — SOURCE: README.md:266
- CLAIM: "Real-time dashboard: span tree, flame graph timeline, message inspector, live over SSE." — SOURCE: README.md:267
- CLAIM: "Any framework: OpenAI, Anthropic, LangChain via callback, or any Python function via `@trace`." — SOURCE: README.md:268
- CLAIM: "Anthropic extended thinking captured: `thinking_blocks` flow into the trace alongside the response." — SOURCE: README.md:269
- CLAIM: "Self-contained HTML export: `agent-lens export <run_id>` shares one file with a colleague, no login, no dashboard needed to view it." — SOURCE: README.md:270,321 and docs/api-reference.md:363-367
- CLAIM: "Secret redaction: Bearer tokens, `sk-*`, `AIza*`, `sk-ant-*` stripped before they hit SQLite." — SOURCE: README.md:271
- CLAIM: "Forked runs are not auto-relaunched" — SOURCE: docs/pause-and-fork.md:213
- CLAIM: "there's no shared or team dashboard" — SOURCE: docs/COMPARISON.md:28
- CLAIM: "There's no hosted offering at all, by design" — SOURCE: docs/COMPARISON.md:29
- CLAIM: "It's built for development and debugging, not production monitoring" — SOURCE: README.md:314-315
- CLAIM: "there's no in-memory database mode — the store always resolves to a real file path, either the default `~/.agent-lens/runs.db` or one you pass in yourself" — SOURCE: docs/api-reference.md:118 (no in-memory mode, must use a file path) and agent_lens/store.py:102-103,108-109 (docstring `store = Store()  # uses default path`; `__init__` defaults `path` to `DEFAULT_DB_PATH` = `~/.agent-lens/runs.db` when no path is given)
- CLAIM: "It's MIT-licensed" — SOURCE: pyproject.toml:10 (`license = { text = "MIT" }`)
- CLAIM: "Python 3.10+" — SOURCE: pyproject.toml:11 (`requires-python = ">=3.10"`); README.md:298 reads "Python 3.10, 3.11, 3.12", not "3.10+", so this claim cites pyproject.toml only
- CLAIM: "https://github.com/RAJUSHANIGARAPU/agent-lens" — SOURCE: pyproject.toml:64-65
