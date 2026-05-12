"""
Example 08 — LangChain: hypothesis testing with fork + diff

The scenario:
  Run A: LangChain chain with a verbose system prompt → wordy answer
  Hypothesis: "Replacing verbose prompt with concise one will reduce token usage"
  Run B: fork with shorter prompt
  GET /diff → verdict: "improved"

No API key needed — uses mock LLM responses.
With an API key: set OPENAI_API_KEY and remove the mock_llm flag.

Install:
    pip install agentlens-tracer langchain langchain-openai

Run:
    python examples/08_langchain_hypothesis.py
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request
import uuid

import agent_lens
from agent_lens.integrations.langchain import AgentLensCallbackHandler
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store

# ---------------------------------------------------------------
# Boot — isolated store so the demo never pollutes real traces
# ---------------------------------------------------------------
tmp_db = tempfile.mktemp(suffix=".db")
store = Store(path=tmp_db)
agent_lens.dashboard.start(store=store, open_browser=False)
time.sleep(0.8)

print("\nagent-lens  ×  LangChain — hypothesis testing demo")
print("Dashboard: http://localhost:7878\n")

QUESTION = "Explain Python's asyncio event loop."

VERBOSE_SYSTEM = (
    "You are an expert Python instructor. Provide comprehensive explanations "
    "covering background, internals, examples, and edge cases. Be thorough."
)
CONCISE_SYSTEM = "Answer in two sentences maximum."

VERBOSE_RESPONSE = (
    "Python's asyncio event loop is the core of its asynchronous I/O framework, "
    "introduced in Python 3.4 as PEP 3156. The event loop continuously checks for "
    "pending coroutines, I/O events, and scheduled callbacks, running each to the "
    "next await point before switching to the next task. This cooperative multitasking "
    "model means that asyncio is single-threaded — only one coroutine runs at a time — "
    "but achieves concurrency by yielding control during I/O waits. Under the hood, "
    "asyncio uses platform-specific selectors (epoll on Linux, kqueue on macOS, IOCP "
    "on Windows) to monitor file descriptors efficiently. Common patterns include "
    "asyncio.gather() for parallel tasks, asyncio.Queue for producer-consumer pipelines, "
    "and asyncio.timeout() (Python 3.11+) for cancellation. Gotcha: blocking calls "
    "(requests, time.sleep) block the whole event loop — always use their async equivalents."
)

CONCISE_RESPONSE = (
    "Python's asyncio event loop is a single-threaded scheduler that runs coroutines "
    "cooperatively, switching between them at each await point. It uses OS-level I/O "
    "multiplexing (epoll/kqueue) to handle thousands of concurrent connections efficiently."
)

now = time.time()

# ---------------------------------------------------------------
# Run A — verbose LangChain chain
# ---------------------------------------------------------------
print("[1/4]  Run A — verbose system prompt")

handler_a = AgentLensCallbackHandler()

# Build mock LangChain trace directly in the store
run_a_id = str(uuid.uuid4())
span_a_id = str(uuid.uuid4())
t_a = now - 4.0

store.save_run(Run(
    id=run_a_id, name="langchain_verbose",
    start_time=t_a, end_time=t_a + 2.341,
    status=RunStatus.COMPLETED,
))
store.save_span(Span(
    id=span_a_id, run_id=run_a_id,
    name="ChatOpenAI", type="llm",
    start_time=t_a, end_time=t_a + 2.341,
))
store.save_event(Event(
    run_id=run_a_id, span_id=span_a_id,
    type=EventType.LLM_START, timestamp=t_a,
    data={"messages": [
        {"role": "system", "content": VERBOSE_SYSTEM},
        {"role": "user", "content": QUESTION},
    ]},
))
store.save_event(Event(
    run_id=run_a_id, span_id=span_a_id,
    type=EventType.LLM_END, timestamp=t_a + 2.341,
    data={
        "latency_ms": 2341,
        "total_tokens": 412,
        "cost_usd": 0.0062,
        "response": {"choices": [{"message": {"content": VERBOSE_RESPONSE}}]},
    },
))
print("  ✓ Run A complete  (412 tokens, $0.0062)")

# ---------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------
print("\n[2/4]  Hypothesis")
print("  'Concise system prompt will reduce tokens without losing accuracy'")
print("  expected_output: 'event loop'")

# ---------------------------------------------------------------
# Run B — concise LangChain chain (the fork)
# ---------------------------------------------------------------
print("\n[3/4]  Run B — fork with concise system prompt")

run_b_id = str(uuid.uuid4())
span_b_id = str(uuid.uuid4())
t_b = now - 1.0

store.save_run(Run(
    id=run_b_id, name="langchain_concise",
    start_time=t_b, end_time=t_b + 0.913,
    status=RunStatus.COMPLETED,
    parent_run_id=run_a_id,
    fork_span_id=span_a_id,
    notes="Hypothesis: concise system prompt will reduce tokens without losing accuracy",
    expected_output="event loop",
))
store.save_span(Span(
    id=span_b_id, run_id=run_b_id,
    name="ChatOpenAI", type="llm",
    start_time=t_b, end_time=t_b + 0.913,
))
store.save_event(Event(
    run_id=run_b_id, span_id=span_b_id,
    type=EventType.LLM_START, timestamp=t_b,
    data={"messages": [
        {"role": "system", "content": CONCISE_SYSTEM},
        {"role": "user", "content": QUESTION},
    ]},
))
store.save_event(Event(
    run_id=run_b_id, span_id=span_b_id,
    type=EventType.LLM_END, timestamp=t_b + 0.913,
    data={
        "latency_ms": 913,
        "total_tokens": 78,
        "cost_usd": 0.00117,
        "response": {"choices": [{"message": {"content": CONCISE_RESPONSE}}]},
    },
))
print("  ✓ Run B complete  (78 tokens, $0.00117)")

# ---------------------------------------------------------------
# Diff
# ---------------------------------------------------------------
print("\n[4/4]  GET /runs/A/diff/B")

diff_url = f"http://localhost:7878/runs/{run_a_id}/diff/{run_b_id}"

for attempt in range(15):
    try:
        with urllib.request.urlopen(diff_url, timeout=3) as resp:
            diff = json.loads(resp.read())
        break
    except Exception:
        if attempt == 14:
            raise
        time.sleep(0.3)

m = diff["metrics_delta"]
print(f"\n  latency    {m['latency_ms']['a']}ms  →  {m['latency_ms']['b']}ms"
      f"  ({m['latency_ms']['pct_change']:+.1f}%)")
print(f"  tokens     {m['total_tokens']['a']}  →  {m['total_tokens']['b']}"
      f"  ({m['total_tokens']['pct_change']:+.1f}%)")
print(f"  cost       ${m['cost_usd']['a']}  →  ${m['cost_usd']['b']}"
      f"  ({m['cost_usd']['pct_change']:+.1f}%)")

ar = diff["assertion_result"]
print(f"\n  assertion  expected_output={ar['expected_output']!r}")
print(f"             passed_in_a={ar['passed_in_a']}  passed_in_b={ar['passed_in_b']}")
print(f"             verdict: {ar['verdict'].upper()}")

print(f"\n  Full diff:  {diff_url}")
print("  Dashboard:  http://localhost:7878")
print("\n  pip install agentlens-tracer\n")
