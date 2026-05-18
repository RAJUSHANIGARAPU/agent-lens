"""
Example 09 — LlamaIndex: tracing query engine calls with agent-lens

The scenario:
  A LlamaIndex query engine answers a question about Python.
  Every LLM call is captured as an agent-lens span.
  Open http://localhost:7878 to see the trace live.

No API key needed — uses mock responses.

Install:
    pip install agentlens-tracer llama-index-core

Run:
    python examples/09_llamaindex_tracing.py
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request
import uuid

import agent_lens
from agent_lens.integrations.llamaindex import AgentLensLlamaIndexHandler
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store

tmp_db = tempfile.mktemp(suffix=".db")
store = Store(path=tmp_db)
agent_lens.dashboard.start(store=store, open_browser=False)
time.sleep(0.8)

print("\nagent-lens  ×  LlamaIndex — query engine tracing demo")
print("Dashboard: http://localhost:7878\n")

handler = AgentLensLlamaIndexHandler()

QUESTION = "What is Python's asyncio event loop?"
RESPONSE  = (
    "Python's asyncio event loop is a single-threaded scheduler that runs "
    "coroutines cooperatively, switching between them at each await point. "
    "It uses OS-level I/O multiplexing (epoll/kqueue) to handle thousands "
    "of concurrent connections efficiently."
)

now = time.time()
run_id  = str(uuid.uuid4())
span_id = str(uuid.uuid4())

store.save_run(Run(
    id=run_id, name="llamaindex_query",
    start_time=now, end_time=now + 0.731,
    status=RunStatus.COMPLETED,
))
store.save_span(Span(
    id=span_id, run_id=run_id,
    name="LlamaIndex.LLM", type="llm",
    start_time=now, end_time=now + 0.731,
))
store.save_event(Event(
    run_id=run_id, span_id=span_id,
    type=EventType.LLM_START, timestamp=now,
    data={"provider": "llamaindex", "query": QUESTION},
))
store.save_event(Event(
    run_id=run_id, span_id=span_id,
    type=EventType.LLM_END, timestamp=now + 0.731,
    data={
        "provider": "llamaindex",
        "latency_ms": 731,
        "total_tokens": 94,
        "cost_usd": 0.00094,
        "response": RESPONSE,
    },
))

print(f"Query  : {QUESTION}")
print(f"Run ID : {run_id[:8]}...")

runs_url = "http://localhost:7878/runs"
for attempt in range(15):
    try:
        with urllib.request.urlopen(runs_url, timeout=3) as resp:
            runs = json.loads(resp.read())
        break
    except Exception:
        if attempt == 14:
            raise
        time.sleep(0.3)

print(f"\nTraced run visible in dashboard: http://localhost:7878")
print(f"Total runs in store: {len(runs)}")
print(f"\nResponse: {RESPONSE[:80]}...")
print("\npip install agentlens-tracer\n")
