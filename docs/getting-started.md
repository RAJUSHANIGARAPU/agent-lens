# Getting Started with agent-lens

## What is agent-lens?

agent-lens is the interactive debugger for LLM agents. Like `pdb` lets you pause and inspect a Python program at any line, agent-lens lets you pause a running LLM agent at any point, inspect its state, edit its messages, and fork a new execution branch — without restarting, without re-running preceding steps.

## Installation

```bash
pip install agent-lens
```

Requirements: Python 3.10+, no Docker, no cloud account, no API keys for the tool itself.

## Quick Start (60 seconds)

```python
import agent_lens
from openai import OpenAI

# One-time: patch OpenAI SDK
agent_lens.install()

# Optional: open the dashboard
agent_lens.dashboard.start()

client = OpenAI()

@agent_lens.trace
def my_agent(query: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": query}]
    )
    return response.choices[0].message.content

result = my_agent("What's the capital of France?")
```

That's it. All LLM calls are now traced and visible in the dashboard at `http://127.0.0.1:7878`.

## Integration Options

### Option 1: Auto-patch (zero code change)

```python
import agent_lens
agent_lens.install()  # patches OpenAI + Anthropic automatically
```

After this single call, all `client.chat.completions.create()` and `client.messages.create()` calls are automatically traced.

### Option 2: @trace decorator

```python
from agent_lens import trace

@trace
def my_function(arg):
    ...
```

Wraps the entire function as a span. Nested `@trace` functions create parent-child span relationships.

### Option 3: Context manager

```python
from agent_lens.tracer import trace_span

with trace_span("my-operation", span_type="llm") as span:
    # ... do work ...
```

### Option 4: LangChain callback

```python
from agent_lens.integrations.langchain import AgentLensCallbackHandler

handler = AgentLensCallbackHandler()
llm = ChatOpenAI(callbacks=[handler])
```

## Framework Support

| Framework | Status | Notes |
|-----------|--------|-------|
| OpenAI SDK | Supported | Auto-patch via `install()` |
| Anthropic SDK | Supported | Auto-patch via `install()` |
| LangChain | Supported | Via callback handler |
| LlamaIndex | Partial | Use `@trace` decorator |
| AutoGen | Partial | Use `@trace` decorator |
| Any Python code | Supported | Use `@trace` decorator |

## Dashboard Features

Open `http://127.0.0.1:7878` after calling `dashboard.start()`.

### Tree View
Collapsible span tree showing:
- Span name and type (LLM, tool, agent, chain)
- Duration
- Status (ok, error, paused)
- Parent-child relationships

### Timeline View
CSS-based flame graph. Spans are rendered as horizontal bars proportional to their duration. Click any bar to open the inspector.

### Inspector View
Selected span details:
- Messages sent to the LLM (user, assistant, system, tool)
- LLM response
- Token counts (prompt + completion)
- Estimated cost
- Raw JSON data

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `/` | Focus search |
| `j/k` | Next/previous span |
| `Space` | Pause/resume |
| `f` | Fork from selected span |
| `1/2/3` | Switch to Tree/Timeline/Inspector |
| `Esc` | Close modal |

## Pause and Fork

See [pause-and-fork.md](pause-and-fork.md) for a detailed walkthrough.

Quick summary:
1. Click **Pause** while an agent is running
2. The agent's thread blocks at the next LLM call
3. Select a span, click **Fork**
4. Edit messages in the modal, click **Fork**
5. New run starts from that point with your edited messages
6. **Resume** to continue the original run

## Data Storage

All data is stored locally in `~/.agent-lens/runs.db` (SQLite). Nothing is sent externally.

```bash
# View database location
python -c "from agent_lens.store import DEFAULT_DB_PATH; print(DEFAULT_DB_PATH)"

# Clear all data
rm ~/.agent-lens/runs.db
```

## Export a Trace

Generate a self-contained HTML file:

```bash
agent-lens export <run_id> --output my_trace.html
```

The file includes all CSS and JavaScript inline — share it without any server.

## Next Steps

- [Pause and Fork deep-dive](pause-and-fork.md)
- [API Reference](api-reference.md)
- [Security model](SECURITY.md)
