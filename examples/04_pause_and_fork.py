"""
Example 04: Pause and Fork — the killer feature showcase

This example programmatically demonstrates the pause-and-fork flow:

1. An "agent" runs in a background thread
2. The main thread pauses it via ControlPlane
3. We inspect the current state
4. We fork the run with edited messages
5. Both the original and forked runs continue independently
6. We compare their outputs

This shows the core debugging workflow WITHOUT needing interactive input —
the whole flow is automated so you can run it as a demo.

Run:
    python examples/04_pause_and_fork.py
"""

import threading
import time
import uuid

import agent_lens
from agent_lens.control import ControlPlane
from agent_lens.models import Run, RunStatus, Span
from agent_lens.store import get_default_store
from agent_lens.tracer import Tracer, trace

# ----------------------------------------------------------------
# Setup
# ----------------------------------------------------------------

print("=" * 60)
print("agent-lens: Pause and Fork Demo")
print("=" * 60)

# Start the dashboard
url = agent_lens.dashboard.start(open_browser=False)
print(f"Dashboard: {url}\n")

tracer = Tracer.get_instance()
cp = ControlPlane.get_instance()
store = get_default_store()

# ----------------------------------------------------------------
# Simulated LLM call (no real API needed)
# ----------------------------------------------------------------

def mock_llm_call(messages: list, run_id: str) -> str:
    """
    Simulates an LLM call. Respects the ControlPlane's pause/inject mechanism.
    Returns an injected result if one is available, otherwise returns a fake response.
    """
    injected = cp.before_llm_call(run_id)
    if injected and "messages" in injected:
        # Forked run with edited messages
        user_message = injected["messages"][-1].get("content", "unknown")
        return f"[FORKED RESPONSE] You asked: '{user_message}'"
    else:
        user_message = messages[-1].get("content", "") if messages else ""
        return f"[ORIGINAL RESPONSE] You asked: '{user_message}'"


# ----------------------------------------------------------------
# The agent
# ----------------------------------------------------------------

class ResearchAgent:
    """A multi-step research agent that can be paused and forked."""

    def __init__(self, name: str, run_id: str):
        self.name = name
        self.run_id = run_id
        self.steps_completed = []
        self.final_answer = None

    def run(self, query: str) -> str:
        print(f"  [{self.name}] Starting research: {query!r}")

        # Step 1: Initial research
        step1_span = tracer.start_span("step-1-research", "llm", run_id=self.run_id)

        messages = [
            {"role": "system", "content": "You are a research assistant."},
            {"role": "user", "content": f"Research topic: {query}"},
        ]

        result1 = mock_llm_call(messages, self.run_id)
        self.steps_completed.append(("step-1", result1))
        print(f"  [{self.name}] Step 1 done: {result1[:60]}...")

        tracer.end_span(step1_span, status="ok", output=result1)

        # Step 2: Follow-up
        step2_span = tracer.start_span("step-2-followup", "llm", run_id=self.run_id)

        messages.append({"role": "assistant", "content": result1})
        messages.append({"role": "user", "content": "Can you elaborate on that?"})

        result2 = mock_llm_call(messages, self.run_id)
        self.steps_completed.append(("step-2", result2))
        print(f"  [{self.name}] Step 2 done: {result2[:60]}...")

        tracer.end_span(step2_span, status="ok", output=result2)

        self.final_answer = f"Research complete: {result1} | {result2}"
        return self.final_answer


# ----------------------------------------------------------------
# Main demo flow
# ----------------------------------------------------------------

def main():
    # --- Phase 1: Start the original agent ---
    print("\n[Phase 1] Starting original agent...")

    original_run = tracer.start_run("original-research-agent")
    print(f"  Original run ID: {original_run.id}")

    # Pause the run BEFORE the agent starts its first LLM call
    cp.pause(original_run.id)
    print("  Run is PAUSED.")

    agent_result = {}
    agent_error = {}

    def run_original_agent():
        try:
            original_agent = ResearchAgent("original", original_run.id)
            result = original_agent.run("What are Python 3.12 features?")
            agent_result["original"] = result
            tracer.end_run(original_run.id, RunStatus.COMPLETED)
            print(f"\n  [original] Agent completed.")
        except Exception as e:
            agent_error["original"] = e
            print(f"  [original] Error: {e}")

    original_thread = threading.Thread(target=run_original_agent, name="original-agent")
    original_thread.daemon = True
    original_thread.start()

    # Give the thread time to start and block on before_llm_call
    time.sleep(0.3)

    print(f"\n  Status: {cp.get_status(original_run.id)}")

    # --- Phase 2: Create a pre-existing span to fork from ---
    print("\n[Phase 2] Creating fork point...")

    # Save a span to the original run to represent "where we are"
    fork_point_span = Span(
        id=str(uuid.uuid4()),
        run_id=original_run.id,
        name="pre-fork-context",
        type="agent",
        start_time=time.time(),
        end_time=time.time() + 0.001,
        status="ok",
    )
    store.save_span(fork_point_span)

    # --- Phase 3: Fork the run with edited messages ---
    print("\n[Phase 3] Forking with edited messages...")

    edited_messages = [
        {"role": "system", "content": "You are a Python expert focused on performance."},
        {"role": "user", "content": "What are the PERFORMANCE features of Python 3.12?"},
    ]

    forked_run_id = cp.fork(
        original_run.id,
        fork_point_span.id,
        edited_messages=edited_messages,
        store=store,
    )
    print(f"  Forked run ID: {forked_run_id}")

    # --- Phase 4: Start the forked agent ---
    print("\n[Phase 4] Starting forked agent...")

    forked_run = store.get_run(forked_run_id)
    assert forked_run is not None

    def run_forked_agent():
        try:
            forked_agent = ResearchAgent("forked", forked_run_id)
            result = forked_agent.run("PERFORMANCE features of Python 3.12?")
            agent_result["forked"] = result
            store.update_run_status(forked_run_id, RunStatus.COMPLETED)
            print(f"\n  [forked] Agent completed.")
        except Exception as e:
            agent_error["forked"] = e
            print(f"  [forked] Error: {e}")

    forked_thread = threading.Thread(target=run_forked_agent, name="forked-agent")
    forked_thread.daemon = True
    forked_thread.start()

    # Wait a moment for the forked thread to start
    time.sleep(0.2)

    # --- Phase 5: Resume the original run ---
    print("\n[Phase 5] Resuming original run...")
    cp.resume(original_run.id)
    print("  Original run RESUMED.")

    # --- Phase 6: Wait for both to complete ---
    print("\n[Phase 6] Waiting for both agents to complete...")
    original_thread.join(timeout=10.0)
    forked_thread.join(timeout=10.0)

    # --- Results ---
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    if "original" in agent_result:
        print(f"\nOriginal run output:")
        print(f"  {agent_result['original'][:120]}...")

    if "forked" in agent_result:
        print(f"\nForked run output:")
        print(f"  {agent_result['forked'][:120]}...")

    if "original" in agent_error:
        print(f"\nOriginal run error: {agent_error['original']}")
    if "forked" in agent_error:
        print(f"\nForked run error: {agent_error['forked']}")

    # Verify divergence
    if "original" in agent_result and "forked" in agent_result:
        diverged = agent_result["original"] != agent_result["forked"]
        print(f"\nDivergence: {'YES ✓' if diverged else 'NO (unexpected)'}")

    print(f"\nDatabase runs:")
    all_runs = store.get_runs(limit=10)
    for r in all_runs:
        fork_info = f" [fork of {r.parent_run_id[:8]}...]" if r.is_fork else ""
        print(f"  {r.id[:8]}... | {r.name} | {r.status}{fork_info}")

    print(f"\nView all traces at: {url}")
    print("\nPress Ctrl+C to stop the dashboard.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
