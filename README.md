```
    _                     _        _
   | |                   | |      | |
   | |     ___ _ __  ___  | |_ ___| |
   | |    / _ \ '_ \/ __| | __/ __| |
   | |___|  __/ | | \__ \ | |_\__ \_|
   |______\___|_| |_|___/  \__|___(_)
```

# **Run the scientific method on your LLM agent.**

State a hypothesis. Fork. Compare. Know if it actually worked.

[![PyPI](https://img.shields.io/pypi/v/agentlens-tracer?color=6366f1)](https://pypi.org/project/agentlens-tracer)
[![CI](https://github.com/RAJUSHANIGARAPU/agent-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/RAJUSHANIGARAPU/agent-lens/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![Coverage](https://codecov.io/gh/RAJUSHANIGARAPU/agent-lens/branch/main/graph/badge.svg)](https://codecov.io/gh/RAJUSHANIGARAPU/agent-lens)
[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/RAJUSHANIGARAPU/agent-lens/blob/main/notebooks/quickstart.ipynb)

---

![agent-lens demo](demo.gif)

> State a hypothesis. Fork. Run `GET /diff`. Get `verdict: "improved"` — with numbers.

---

## What is agent-lens?

A local-first debugger for LLM agents that turns vibes-based prompt iteration into a measurable experiment.

The standard loop today:
1. Agent fails. You guess what went wrong.
2. Edit the code, restart, wait through every step again.
3. Look at the new output. Decide if it's better. Repeat.

You burn 10 minutes per hypothesis and you have no record of *why* you made each change. agent-lens replaces this with:

1. **Pause** the running agent at any LLM call.
2. State a **hypothesis** (`notes: "shorter system prompt should reduce hallucination"`) and an **expected outcome** (`expected_output: "concise"`).
3. **Fork** with edited messages. The original keeps running.
4. **Diff** the two runs. Get `verdict: improved`, `regressed`, or `neither_pass` — and a structural diff of every message, response, and metric.

You're left with a versioned record of every hypothesis you tested. Future-you (or your teammate) can read your reasoning, not just see the final code.

---

## Install in 30 seconds

```bash
pip install agentlens-tracer
```

```python
import agent_lens
from openai import OpenAI

agent_lens.install()          # auto-patch OpenAI + Anthropic + LangChain
agent_lens.dashboard.start()  # localhost:7878

client = OpenAI()

@agent_lens.trace
def my_agent(query: str) -> str:
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": query}]
    ).choices[0].message.content

my_agent("Explain Python 3.12 typing improvements")
```

Dashboard opens. Every LLM call is traced. Pause, fork, diff — all from the browser or the API.

---

## The four killer endpoints

No other observability tool — Langfuse, LangSmith, Phoenix, AgentOps, Helicone — has any of these.

### 1. Fork with hypothesis + assertion

```http
POST /runs/{run_id}/fork
{
  "span_id": "abc123",
  "edited_messages": [{"role": "system", "content": "Be concise."}],
  "notes": "Hypothesis: removing role constraint will reduce verbosity",
  "expected_output": "concise"
}
```

Records the *why* alongside the *what*. The note travels with the run forever.

### 2. Compare any two runs

```http
GET /runs/{run_a}/diff/{run_b}
```

Returns:

```json
{
  "messages_diff": [{"role": "system", "a": "...", "b": "...", "changed": true}],
  "metrics_delta": {
    "latency_ms":   {"a": 1200, "b": 800,  "delta": -400, "pct_change": -33.3},
    "total_tokens": {"a": 450,  "b": 180,  "delta": -270, "pct_change": -60.0},
    "cost_usd":     {"a": 0.0045, "b": 0.0018, "delta": -0.0027}
  },
  "response_diff":  {"a": "Verbose answer...", "b": "Concise answer.", "changed": true},
  "thinking_blocks": {"a": [], "b": ["Let me reason step by step..."]},
  "assertion_result": {
    "expected_output": "concise",
    "passed_in_a": false,
    "passed_in_b": true,
    "verdict": "improved"
  }
}
```

Hypothesis confirmed. With numbers. In one HTTP call.

### 3. Trace the lineage of every fork

```http
GET /runs/{run_id}/lineage
```

Walks the full ancestry chain. Useful when you've forked five times trying to fix the same bug — see every hypothesis in chronological order.

### 4. Annotate any run after the fact

```http
POST /runs/{run_id}/note
{ "notes": "This was the run that finally worked. Reason: temperature=0.2." }
```

Build institutional knowledge into your trace database, not your Slack DMs.

---

## Pause and fork — the runtime control plane

```
[Agent running] → click Pause → agent blocks at next LLM call
                                ↓
                          [Edit messages in dashboard]
                                ↓
                          click Fork → new run diverges
                                ↓
                          click Resume → original continues
                                ↓
                  [Two runs, side by side. GET /diff to compare.]
```

No restarts. No re-running preceding steps. Programmatic too:

```python
from agent_lens.control import ControlPlane

cp = ControlPlane.get_instance()
cp.pause(run_id)
new_run_id = cp.fork(
    run_id=run_id,
    span_id=span_id,
    edited_messages=[{"role": "user", "content": "Different question"}],
    notes="Trying with explicit instructions",
    expected_output="step-by-step",
)
cp.resume(run_id)
```

---

## Why this matters

You're not debugging a function — you're debugging a probabilistic system. Every prompt change is a *hypothesis test*: "this change should improve X without breaking Y." Today, you run that test by eyeballing two outputs in two terminal windows. agent-lens makes the test structural, repeatable, and recorded.

> Vibes-based prompt engineering is debugging without the debugger.
> agent-lens is the debugger.

---

## What else you get

- **Zero infrastructure** — SQLite at `~/.agent-lens/runs.db`. No Docker. No cloud. No tool API keys.
- **Real-time dashboard** — span tree, flame graph timeline, message inspector. Live via SSE.
- **Any framework** — OpenAI, Anthropic, LangChain via callback. Any Python function via `@trace`.
- **Anthropic extended thinking captured** — `thinking_blocks` flow into your traces alongside the response.
- **Self-contained HTML export** — share a single file with a colleague. No login. No dashboard required to view it.
- **Secret redaction** — Bearer tokens, `sk-*` keys, `AIza*`, `sk-ant-*` — stripped before they hit SQLite.

---

## How agent-lens compares

| Feature                               | agent-lens | Langfuse        | LangSmith |
|---------------------------------------|:----------:|:---------------:|:---------:|
| Local-first (no cloud)                | ✅         | Partial         | ❌        |
| Pause live agent mid-run              | ✅         | ❌              | ❌        |
| Fork from any LLM call                | ✅         | ❌              | ❌        |
| **Structural run diff**               | ✅         | ❌              | ❌        |
| **Hypothesis + expected_output**      | ✅         | ❌              | ❌        |
| **Fork lineage trace**                | ✅         | ❌              | ❌        |
| Real-time dashboard                   | ✅         | ✅              | ✅        |
| Multi-framework (OpenAI/Claude/LC)    | ✅         | ✅              | Partial   |
| Data stays on your machine            | ✅         | ❌              | ❌        |
| Zero-infrastructure setup             | ✅         | ❌              | ❌        |
| Secret redaction by default           | ✅         | Partial         | Partial   |
| Anthropic extended thinking captured  | ✅         | ❌              | ❌        |

---

## Compatibility

- Python 3.10, 3.11, 3.12
- OpenAI SDK ≥ 1.0
- Anthropic SDK ≥ 0.20
- LangChain ≥ 0.1 (optional)
- macOS, Linux, Windows

---

## FAQ

**Does it work without OpenAI or Anthropic?**
Yes. Use `@agent_lens.trace` on any Python function. The SDK integrations are optional.

**Does my data leave my machine?**
No. All data is stored in `~/.agent-lens/runs.db`. No telemetry, no callbacks, no network egress.

**Is it production-safe?**
It's designed for development and debugging. The overhead is < 5ms per traced call on local hardware. The dashboard server binds to 127.0.0.1 only — it's not exposed to the network.

**What happens when I restart the dashboard?**
Traces persist in SQLite. Reload the dashboard — your previous runs and forks are still there, with all their notes intact.

**Can I share a trace with a colleague?**
Yes: `agent-lens export <run_id> --output trace.html` generates a self-contained HTML file. Email it, drop it in Slack, archive it in your repo. No agent-lens install needed to view.

**Does the run diff work between unrelated runs, or only fork pairs?**
Any two runs. The endpoint is `GET /runs/{a}/diff/{b}` — useful for comparing the same prompt across model versions, or two production runs with different inputs.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports, feature requests, and PRs all welcome.

## License

MIT — see [LICENSE](LICENSE).
