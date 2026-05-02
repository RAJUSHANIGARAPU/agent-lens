# Pause and Fork: Deep Dive

The pause-and-fork feature is the reason agent-lens exists. This document explains the concept, the implementation, and how to use it effectively.

## The Problem It Solves

When debugging a multi-step agent, you often want to answer: "What would have happened if the LLM had received *this* message instead of *that* one at step 3?"

Without pause-and-fork, the answer requires:
1. Edit the source code to hardcode the modified input
2. Re-run the entire agent from the beginning
3. Wait through all the preceding steps
4. Check the result
5. Repeat until you find the right modification

A 5-step agent with 4-second LLM calls = 20 seconds per hypothesis. 10 hypotheses = 3.5 minutes of waiting, minimum.

With pause-and-fork:
1. Pause the running agent at step 3
2. Edit the message in the dashboard
3. Fork — a new run starts from step 3 with your edited message
4. Both runs continue in parallel
5. Compare results in the same dashboard view

## How It Works

### The Pause Mechanism

```
Agent thread                       ControlPlane
─────────────                      ────────────
...agent code...
call before_llm_call(run_id)  ──►  check _pause_events[run_id]
                                   event is SET (paused)
thread BLOCKS ◄──────────────────  event.wait() returns only when cleared
...                                
(Dashboard calls POST /resume)
                                   event.clear()
thread UNBLOCKS ◄────────────────  
continue execution
```

The pause is implemented with a `threading.Event` per `run_id`:
- `pause(run_id)` → `event.set()` (set = paused, agent waits)
- `resume(run_id)` → `event.clear()` (clear = running, agent continues)

The tracer calls `before_llm_call(run_id)` before every LLM call. This is the checkpoint where the agent can be paused.

### The Fork Mechanism

When you fork a run:
1. A new `Run` record is created with `parent_run_id = original_run_id` and `fork_span_id = the_span_you_forked_from`
2. The new run's ID is returned
3. If you provided edited messages, they are stored in the `ControlPlane` as an "inject result" for the new run

When the forked run makes its first LLM call:
1. `before_llm_call(new_run_id)` is called
2. The `ControlPlane` finds the injected messages
3. The injected messages are returned to the tracer instead of a `None`
4. The tracer (or integration) uses the injected messages as the LLM input

**The key insight**: The forked run doesn't re-run preceding steps. It starts from the fork point and diverges from there. All spans before the fork point are shared by reference — no data is duplicated.

### Span Sharing (no duplication)

```
Original run:    [span-A] → [span-B] → [span-C] → [span-D] ...
                                          ^
                                     fork point

Forked run:      [span-A] → [span-B] → [span-C'] → [span-D'] ...
                                          ^
                                   shares A, B, C by reference
                                   span-C' is a new span with edited input
```

In the database, `get_spans(forked_run_id, include_parent_spans=True)` queries:
- Spans from the parent run up to and including `fork_span_id`
- Spans from the forked run itself

No data is copied. The foreign key `parent_run_id` + `fork_span_id` is sufficient to reconstruct the full trace.

## Using Pause and Fork

### Via the Dashboard (recommended)

1. Start your agent with tracing enabled.
2. Click **Pause** in the top bar (or press `Space`).
3. The agent's thread will block at the next LLM call. The status badge changes to `paused` (yellow).
4. In the Tree view, select the span you want to fork from.
5. Click **Fork** (or press `F`). The fork modal opens.
6. The modal pre-populates with the messages from the selected LLM call.
7. Edit the messages as needed.
8. Click **Fork**. A new run appears in the left panel.
9. Click **Resume** to continue the original run.

Both runs now execute in parallel. You can inspect them side-by-side in the dashboard.

### Via the API

```python
from agent_lens.control import ControlPlane

cp = ControlPlane.get_instance()

# Pause a running agent
cp.pause(run_id)

# Fork from a specific span
new_run_id = cp.fork(
    run_id=run_id,
    span_id=span_id,
    edited_messages=[
        {"role": "system", "content": "You are a more focused assistant."},
        {"role": "user", "content": "Research Python 3.12 ONLY performance features"},
    ]
)

# Resume the original
cp.resume(run_id)
```

### Via the HTTP API

```bash
# Get CSRF token (printed on startup)
TOKEN="your-csrf-token"
RUN_ID="your-run-id"
SPAN_ID="the-span-to-fork-from"

# Pause
curl -X POST http://127.0.0.1:7878/runs/$RUN_ID/pause \
  -H "X-Agent-Lens-Token: $TOKEN"

# Fork
curl -X POST http://127.0.0.1:7878/runs/$RUN_ID/fork \
  -H "X-Agent-Lens-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "span_id": "'$SPAN_ID'",
    "edited_messages": [
      {"role": "user", "content": "edited question"}
    ]
  }'

# Resume
curl -X POST http://127.0.0.1:7878/runs/$RUN_ID/resume \
  -H "X-Agent-Lens-Token: $TOKEN"
```

## Step Mode

"Step" lets you advance exactly one LLM call, then re-pause automatically:

```python
cp.step(run_id, num_calls=1)
```

Or click the **Step** button in the dashboard. Useful for walking through an agent call-by-call.

## Inject

Inject a synthetic LLM response without making a real API call:

```python
cp.inject(run_id, {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "This is a synthetic response"
        }
    }]
})
```

The agent receives this synthetic response and continues. This is useful for:
- Testing edge cases (e.g., what if the LLM returns an empty response?)
- Injecting tool results
- Bypassing API calls in integration tests

## Common Patterns

### Debugging a bad decision at step N

```
Agent run → step 1 ok → step 2 ok → step 3 wrong → step 4 fails
```

1. Pause before step 3
2. Fork from step 2 span
3. Edit step 2's output (or step 3's input messages) in the fork
4. Resume both
5. Observe: forked run takes a different path at step 3

### Testing robustness to a specific response

1. Pause at an LLM call
2. Inject: `cp.inject(run_id, {"choices": [{"message": {"content": "I don't know"}}]})`
3. Resume
4. Observe how the agent handles an "I don't know" response

### A/B testing prompts

1. Run agent once (gets result A)
2. Fork from the first LLM span with a different system prompt
3. Run forked agent (gets result B)
4. Compare A vs B in the dashboard

## Limitations

- **Only works on LLM calls** — the pause checkpoint is `before_llm_call()`. Tool execution, vector DB queries, and Python function calls cannot be intercepted mid-execution (though you can pause before the next LLM call that follows them).
- **Not async-aware for blocking** — `before_llm_call()` uses `threading.Event.wait()`, which blocks a thread. In fully async code (no threads), use `asyncio.Event` instead (planned for v0.2).
- **Forked runs must be started manually** — the fork creates a new Run record but doesn't automatically launch a new agent process. You are responsible for starting the forked agent (e.g., by calling the agent function with the forked run ID).
