# First Run Walkthrough

This guide takes you from zero to a working agent-lens trace in under 5 minutes.

## Step 1: Install

```bash
pip install agentlens
```

Verify installation:

```bash
agent-lens version
# agent-lens 0.1.0
```

## Step 2: Instrument your agent

If you're using the OpenAI SDK, the fastest path is `install()`:

```python
import agent_lens
agent_lens.install()  # auto-patches OpenAI + Anthropic SDKs
```

Or use the `@trace` decorator explicitly:

```python
import agent_lens

@agent_lens.trace
def my_agent(query: str) -> str:
    # ... your agent code ...
    return result
```

## Step 3: Start the dashboard

```python
agent_lens.dashboard.start()
# Prints: agent-lens dashboard running at http://127.0.0.1:7878
```

Or from the command line:

```bash
agent-lens dashboard
```

Your browser opens automatically. You'll see the dashboard with an empty run list.

## Step 4: Run your agent

Call your agent function. With `install()`, any OpenAI or Anthropic calls are automatically captured. With `@trace`, the entire function call becomes a span.

```python
result = my_agent("What are the main features of Python 3.12?")
```

The dashboard updates in real time as spans are created and completed.

## Step 5: Inspect the trace

In the dashboard:

1. **Left panel** — Your run appears with a status badge (running/completed/error).
2. **Tree tab** — Collapsible span tree. Click any span to select it.
3. **Inspector tab** — See the messages sent to the LLM, the response, token counts, and latency.
4. **Timeline tab** — Flame graph view of all spans by time.

## Step 6: Try Pause and Fork (optional)

The killer feature. While an agent is running:

1. Click **Pause** in the top bar (or press `Space`).
2. The agent's thread blocks at the next LLM call.
3. Select the span you want to fork from.
4. Click **Fork** (or press `F`).
5. Edit the messages in the fork modal.
6. Click **Fork** — a new run appears in the left panel.
7. Click **Resume** to let the original run continue.

Both runs now execute independently with different starting points.

## Step 7: Export a trace (optional)

```bash
agent-lens export <run_id> --output my_trace.html
```

This creates a self-contained HTML file with all trace data embedded. Share it with colleagues without any server or cloud account.

## Common issues

**Dashboard doesn't open automatically**

Open `http://127.0.0.1:7878` manually.

**No runs appearing in the dashboard**

Check that you called `agent_lens.install()` (for auto-patching) or added `@agent_lens.trace` to your function. Also verify the function was actually called after tracing was set up.

**"Run not found" on control operations**

The CSRF token changes on each server restart. Refresh the browser after restarting the dashboard server.

**Events stream shows "disconnected"**

The SSE connection dropped. The dashboard will automatically reconnect within 3 seconds.

## Where is the data stored?

```
~/.agent-lens/runs.db
```

SQLite database. All data is local. Nothing is sent to any external service.

Delete it to start fresh:
```bash
rm ~/.agent-lens/runs.db
```
