"""
agent-lens pure-CLI demo — optimized for asciinema / terminal recording.

Single-command demo that prints a complete hypothesis-driven debugging story
in the terminal. No browser. No window switching. Just one cinematic flow:

    Run A (verbose)  →  Hypothesis  →  Run B (concise)  →  Diff  →  IMPROVED

Usage:
    export OPENAI_API_KEY=sk-...
    python examples/06_demo_cli.py

For asciinema recording:
    asciinema rec demo.cast -c "python examples/06_demo_cli.py"
    agg demo.cast demo.gif --theme monokai --font-size 18
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from openai import OpenAI

import agent_lens
from agent_lens.store import get_default_store

# ---------------------------------------------------------------
# ANSI colors — make the verdict pop
# ---------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RED = "\033[31m"
BG_GREEN = "\033[42m"
BG_BLACK = "\033[40m"
WHITE = "\033[97m"

# ---------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------
if not os.environ.get("OPENAI_API_KEY"):
    sys.stderr.write(f"{RED}ERROR: set OPENAI_API_KEY before running.{RESET}\n")
    sys.exit(1)


def section(title: str, color: str = CYAN) -> None:
    line = "─" * 60
    print(f"\n{color}{line}{RESET}")
    print(f"{color}{BOLD}  {title}{RESET}")
    print(f"{color}{line}{RESET}\n")


def typed(text: str, delay: float = 0.015) -> None:
    """Type-out animation for cinematic terminal feel."""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")


# ---------------------------------------------------------------
# Boot
# ---------------------------------------------------------------
print(f"\n{BOLD}{MAGENTA}  agent-lens{RESET}  {DIM}— scientific method for LLM agents{RESET}\n")

agent_lens.install()
agent_lens.dashboard.start()
time.sleep(1.0)

client = OpenAI()
QUESTION = "What is Python's GIL?"

# ---------------------------------------------------------------
# Run A: verbose
# ---------------------------------------------------------------
section("[1/4]  Run A — verbose system prompt", YELLOW)
typed(f"  {DIM}prompt:{RESET}  Be thorough and pedagogical. Cover background, examples, edge cases.")
typed(f"  {DIM}query:{RESET}   {QUESTION}")
print()

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

verbose_agent()
print(f"  {GREEN}✓ Run A complete{RESET}")

# ---------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------
section("[2/4]  Hypothesis", MAGENTA)
typed(f"  {YELLOW}A shorter system prompt will produce a concise response.{RESET}")
typed(f"  {DIM}expected_output:{RESET}  {BOLD}'concise'{RESET}")

# ---------------------------------------------------------------
# Run B: concise
# ---------------------------------------------------------------
section("[3/4]  Run B — fork with edited system prompt", YELLOW)
typed(f"  {DIM}prompt:{RESET}  Answer in one short sentence.")
typed(f"  {DIM}query:{RESET}   {QUESTION}")
print()

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

concise_agent()
print(f"  {GREEN}✓ Run B complete{RESET}")

# ---------------------------------------------------------------
# Link Run B as a fork of Run A with notes + expected_output
# ---------------------------------------------------------------
store = get_default_store()
runs = sorted(store.get_runs(), key=lambda r: r.start_time)
run_a, run_b = runs[-2], runs[-1]


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
# Diff
# ---------------------------------------------------------------
section("[4/4]  GET /runs/A/diff/B", CYAN)
diff_url = f"http://localhost:7878/runs/{run_a.id}/diff/{run_b.id}"
typed(f"  {DIM}$ curl -s {diff_url[:50]}...{RESET}")
print()

with urllib.request.urlopen(diff_url) as resp:
    diff = json.loads(resp.read())

# Print metrics deltas
metrics = diff["metrics_delta"]
print(f"  {BOLD}Metrics delta{RESET}")
for key in ("latency_ms", "total_tokens", "cost_usd"):
    m = metrics.get(key, {})
    a, b, delta, pct = m.get("a"), m.get("b"), m.get("delta"), m.get("pct_change")
    if a is None or b is None:
        continue
    sign = GREEN if (delta or 0) < 0 else RED
    pct_str = f"({sign}{pct:+.1f}%{RESET})" if pct is not None else ""
    print(f"    {DIM}{key:<14}{RESET}  {a}  →  {b}  {pct_str}")

# Print response diff (truncated)
resp_diff = diff["response_diff"]
print(f"\n  {BOLD}Response{RESET}")
print(f"    {DIM}A:{RESET}  {(resp_diff['a'] or '')[:90]}{DIM}...{RESET}")
print(f"    {DIM}B:{RESET}  {(resp_diff['b'] or '')[:90]}")

# THE PUNCHLINE
ar = diff["assertion_result"]
print(f"\n  {BOLD}Assertion{RESET}")
print(f"    {DIM}expected_output:{RESET}  {ar['expected_output']!r}")
print(f"    {DIM}passed in A:{RESET}     {RED}✗ false{RESET}" if not ar["passed_in_a"] else f"    {DIM}passed in A:{RESET}     {GREEN}✓ true{RESET}")
print(f"    {DIM}passed in B:{RESET}     {GREEN}✓ true{RESET}" if ar["passed_in_b"] else f"    {DIM}passed in B:{RESET}     {RED}✗ false{RESET}")

verdict = ar["verdict"]
print()
if verdict == "improved":
    print(f"  {BG_GREEN}{WHITE}{BOLD}                                                        {RESET}")
    print(f"  {BG_GREEN}{WHITE}{BOLD}              VERDICT: HYPOTHESIS CONFIRMED            {RESET}")
    print(f"  {BG_GREEN}{WHITE}{BOLD}                  verdict: \"improved\"                  {RESET}")
    print(f"  {BG_GREEN}{WHITE}{BOLD}                                                        {RESET}")
else:
    print(f"  {BOLD}verdict:{RESET} {verdict}")

print(f"\n  {DIM}Full diff:  {diff_url}{RESET}")
print(f"  {DIM}Dashboard:  http://localhost:7878{RESET}\n")
print(f"  {BOLD}{MAGENTA}pip install agentlens-tracer{RESET}\n")
