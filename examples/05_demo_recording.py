"""
agent-lens demo recording script.

Produces a clean, repeatable scenario for the launch demo video:
  1. Run A — verbose system prompt → wordy response (~250 tokens)
  2. Run B — forked with hypothesis "shorter prompt → concise" → short response (~25 tokens)
  3. Diff endpoint returns: verdict: "improved"

Usage:
  export OPENAI_API_KEY=sk-...
  python examples/05_demo_recording.py

Then in a second terminal (or same terminal after Ctrl+C the dashboard):
  curl -s http://localhost:7878/runs/{run_a}/diff/{run_b} | jq

The script prints both run IDs and the exact curl command at the end.
"""

from __future__ import annotations

import os
import sys
import time

from openai import OpenAI

import agent_lens
from agent_lens.store import get_default_store

# ---------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------
if not os.environ.get("OPENAI_API_KEY"):
    print("ERROR: set OPENAI_API_KEY before running.", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------
# Bootstrap agent-lens
# ---------------------------------------------------------------
agent_lens.install()                  # auto-patch OpenAI
agent_lens.dashboard.start()          # http://localhost:7878
time.sleep(1.5)                       # give the server a moment

print("\n  Dashboard: http://localhost:7878\n")

client = OpenAI()
QUESTION = "What is Python's GIL?"

# ---------------------------------------------------------------
# RUN A — verbose system prompt
# ---------------------------------------------------------------
VERBOSE_SYSTEM = (
    "You are an expert technical assistant. When asked a question, provide "
    "comprehensive explanations covering all relevant background, historical "
    "context, examples, edge cases, and trade-offs. Be thorough and pedagogical."
)

@agent_lens.trace
def verbose_agent():
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": VERBOSE_SYSTEM},
            {"role": "user", "content": QUESTION},
        ],
    )

print("  [1/3] Run A — verbose system prompt ...")
verbose_agent()

# ---------------------------------------------------------------
# RUN B — concise system prompt (the hypothesis)
# ---------------------------------------------------------------
CONCISE_SYSTEM = "Answer in one short sentence."

@agent_lens.trace
def concise_agent():
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": CONCISE_SYSTEM},
            {"role": "user", "content": QUESTION},
        ],
    )

print("  [2/3] Run B — hypothesis: shorter prompt -> concise response ...")
concise_agent()

# ---------------------------------------------------------------
# Link Run B as a fork of Run A with notes + expected_output assertion
# ---------------------------------------------------------------
store = get_default_store()

runs = sorted(store.get_runs(), key=lambda r: r.start_time)
run_a, run_b = runs[-2], runs[-1]

# Find the LLM span in Run A — that's the fork point
def _first_llm_span_id(spans) -> str | None:
    for s in spans:
        if getattr(s, "type", None) == "llm":
            return s.id
        for child in getattr(s, "children", []) or []:
            found = _first_llm_span_id([child])
            if found:
                return found
    return None

run_b.parent_run_id = run_a.id
run_b.fork_span_id = _first_llm_span_id(store.get_spans(run_a.id))
run_b.notes = "Hypothesis: a shorter system prompt will produce a concise response"
run_b.expected_output = "concise"
store.save_run(run_b)

# ---------------------------------------------------------------
# Print the punchline command
# ---------------------------------------------------------------
print("\n  [3/3] Done.\n")
print(f"  Run A (verbose) : {run_a.id}")
print(f"  Run B (concise) : {run_b.id}")
print(f"  Hypothesis      : {run_b.notes}")
print(f"  Expected output : {run_b.expected_output!r}")
print()
print("  ---- THE PUNCHLINE SHOT ----")
print()
print(f'  curl -s http://localhost:7878/runs/{run_a.id}/diff/{run_b.id} | jq')
print()
print('  Look for:  "verdict": "improved"')
print()
print("  Press Ctrl+C to stop the dashboard.")

# Keep the dashboard alive so you can record it
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n  Stopped.")
