```
    _                     _        _
   | |                   | |      | |
   | |     ___ _ __  ___  | |_ ___| |
   | |    / _ \ '_ \/ __| | __/ __| |
   | |___|  __/ | | \__ \ | |_\__ \_|
   |______\___|_| |_|___/  \__|___(_)
```

**The interactive debugger for AI agents.**

[![PyPI](https://img.shields.io/pypi/v/agentlens-tracer?color=6366f1)](https://pypi.org/project/agentlens-tracer)
[![CI](https://github.com/RAJUSHANIGARAPU/agent-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/RAJUSHANIGARAPU/agent-lens/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![Coverage](https://codecov.io/gh/RAJUSHANIGARAPU/agent-lens/branch/main/graph/badge.svg)](https://codecov.io/gh/RAJUSHANIGARAPU/agent-lens)

---

> **Demo GIF** — *Pause a live agent, edit its messages, fork a new run, compare results. No restarts.*
>
> *(Demo recording in progress — see [docs/demo-storyboard.md](docs/demo-storyboard.md))*

---

## The Problem

Your LLM agent fails at step 4. You suspect the issue is at step 2. To test a fix, you edit the code, restart the agent, wait through steps 1–3 again, and check. Ten hypotheses = ten restarts. A 30-second agent = five minutes of waiting just to test one idea.

Every observability tool (LangSmith, Langfuse, Phoenix, AgentOps) shows you what happened after the fact. None of them let you **pause the agent mid-run, edit its state, and fork a new execution from that exact point.**

agent-lens does.

## Install and Use (5 lines)

```bash
pip install agentlens-tracer
```

```python
import agent_lens
from openai import OpenAI

agent_lens.install()          # auto-patch OpenAI + Anthropic
agent_lens.dashboard.start()  # open dashboard at localhost:7878

client = OpenAI()

@agent_lens.trace
def my_agent(query: str) -> str:
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": query}]
    ).choices[0].message.content

my_agent("What are the key features of Python 3.12?")
```

Dashboard opens automatically. All LLM calls are traced.

## What You Get

- **Zero infrastructure** — SQLite database at `~/.agent-lens/runs.db`. No Docker. No cloud account. No API keys for the tool itself.
- **Real-time dashboard** — Span tree, flame graph timeline, message inspector. Updates live via SSE as your agent runs.
- **Any framework** — OpenAI, Anthropic, LangChain via callback handler, or any Python function via `@trace`.

## Pause and Fork — The Killer Feature

```
[Agent running] → click Pause → agent blocks at next LLM call
                                ↓
                          [Edit messages in dashboard]
                                ↓
                          click Fork → new run diverges from this point
                                ↓
                          click Resume → original continues
                                ↓
         [Two runs, different paths, side by side in dashboard]
```

No restarts. No re-running preceding steps. Change one variable, see what diverges.

**Via the API**:

```python
from agent_lens.control import ControlPlane

cp = ControlPlane.get_instance()
cp.pause(run_id)

new_run_id = cp.fork(
    run_id=run_id,
    span_id=span_id,
    edited_messages=[
        {"role": "user", "content": "Different question"}
    ]
)

cp.resume(run_id)
```

## Comparison

| Feature | agent-lens | Langfuse | LangSmith |
|---------|-----------|----------|-----------|
| Local-first (no cloud) | ✅ | Partial (self-host) | ❌ |
| Pause live agent | ✅ | ❌ | ❌ |
| Fork from any point | ✅ | ❌ | ❌ |
| Real-time dashboard | ✅ | ✅ | ✅ |
| Multi-framework | ✅ | ✅ | Partial |
| Data stays on machine | ✅ | ❌ | ❌ |
| Zero-infra setup | ✅ | ❌ | ❌ |
| Secret redaction | ✅ | Partial | Partial |

## Compatibility

- Python 3.10, 3.11, 3.12
- OpenAI SDK ≥ 1.0
- Anthropic SDK ≥ 0.20
- LangChain ≥ 0.1 (optional)
- macOS, Linux, Windows

## FAQ

**Does it work without OpenAI or Anthropic?**
Yes. Use `@agent_lens.trace` on any Python function. The SDK integrations are optional.

**Does my data leave my machine?**
No. All data is stored in `~/.agent-lens/runs.db`. No telemetry, no callbacks, no network egress.

**Is it production-safe?**
It's designed for development and debugging. The overhead is < 5ms per traced call. The dashboard server binds to 127.0.0.1 only — it's not exposed to the network.

**What happens when I restart the dashboard?**
Traces persist in SQLite. Reload the dashboard and your previous runs are still there.

**Can I share a trace with a colleague?**
Yes: `agent-lens export <run_id> --output trace.html` generates a self-contained HTML file you can email or share via any file-sharing tool.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions welcome.

## License

MIT — see [LICENSE](LICENSE).
