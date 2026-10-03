"""
agent-lens mock CLI demo — no OpenAI key required.

Uses pre-canned responses to produce a deterministic, repeatable terminal
demo. Perfect for asciinema / VHS recording without burning API credits.

Run A is a live run: it is paused mid-flight, inspected, and resumed before the
fork and diff are shown.

Usage:
    python examples/07_demo_mock.py

For VHS recording:
    vhs demo.tape
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

import agent_lens
from agent_lens.control import ControlPlane
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.store import Store

# ---------------------------------------------------------------
# ANSI colors
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
WHITE = "\033[97m"


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
print(f"\n{BOLD}{MAGENTA}  agent-lens{RESET}  {DIM}— the framework-agnostic, local-first debugger for LLM agents{RESET}\n")

tmp_db = tempfile.mktemp(suffix=".db")
store = Store(path=tmp_db)
agent_lens.dashboard.start(store=store, open_browser=False)
time.sleep(1.0)

QUESTION = "What is Python's GIL?"

VERBOSE_SYSTEM = (
    "You are an expert technical assistant. When asked a question, provide "
    "comprehensive explanations covering all relevant background, historical "
    "context, examples, edge cases, and trade-offs. Be thorough and pedagogical."
)
CONCISE_SYSTEM = "Answer in one short sentence."

VERBOSE_RESPONSE = (
    "The Global Interpreter Lock (GIL) in Python is a mutex that protects access "
    "to Python objects, preventing multiple threads from executing Python bytecodes "
    "simultaneously.\n\n"
    "Historical context: Introduced in CPython as a simpler alternative to fine-grained "
    "locking. The CPython memory manager is not thread-safe, so the GIL provides a "
    "simple mechanism to protect it.\n\n"
    "Impact on threading: The GIL prevents true CPU-level parallelism in Python threads, "
    "but does not block I/O-bound concurrency. When a thread performs I/O (file reads, "
    "network calls), it releases the GIL, allowing other threads to run.\n\n"
    "Workarounds: Use multiprocessing (each process has its own GIL), or C extensions "
    "that release the GIL during heavy computation (NumPy, SciPy). Python 3.13 introduces "
    "experimental free-threaded mode (--disable-gil) as PEP 703."
)

# "concise" appears here so the assertion passes deterministically
CONCISE_RESPONSE = (
    "Python's GIL is a concise mutex that ensures only one thread executes Python "
    "bytecode at a time, preventing true parallelism but simplifying memory management."
)

# ---------------------------------------------------------------
# Run A: verbose
# ---------------------------------------------------------------
section("[1/5]  Run A — verbose system prompt", YELLOW)
typed(f"  {DIM}prompt:{RESET}  Be thorough and pedagogical. Cover background, examples, edge cases.")
typed(f"  {DIM}query:{RESET}   {QUESTION}")
print()

run_a_id = str(uuid.uuid4())
span_a_id = str(uuid.uuid4())
t_a = time.time()

# Run A starts RUNNING with no end_time; a worker thread drives it through the
# control plane so it can really be paused at the LLM call.
store.save_run(Run(
    id=run_a_id, name="verbose_agent",
    start_time=t_a, status=RunStatus.RUNNING,
))

cp = ControlPlane.get_instance()
cp.set_store(store)
started = threading.Event()


def run_a_worker() -> None:
    store.save_span(Span(
        id=span_a_id, run_id=run_a_id, name="chat.completions", type="llm",
        start_time=t_a,
    ))
    store.save_event(Event(
        run_id=run_a_id, span_id=span_a_id, type=EventType.LLM_START, timestamp=t_a,
        data={"messages": [
            {"role": "system", "content": VERBOSE_SYSTEM},
            {"role": "user", "content": QUESTION},
        ]},
    ))
    started.set()
    time.sleep(0.4)  # building the request
    cp.before_llm_call(run_a_id)  # blocks here while the run is paused
    store.save_event(Event(
        run_id=run_a_id, span_id=span_a_id, type=EventType.LLM_END, timestamp=t_a + 1.847,
        data={
            "latency_ms": 1847, "total_tokens": 453, "cost_usd": 0.0045,
            "response": {"choices": [{"message": {"content": VERBOSE_RESPONSE}}]},
        },
    ))
    store.save_span(Span(
        id=span_a_id, run_id=run_a_id, name="chat.completions", type="llm",
        start_time=t_a, end_time=t_a + 1.847,
    ))


worker = threading.Thread(target=run_a_worker, name="run-a-worker", daemon=True)


def count_llm_end(run_id: str) -> int:
    return sum(1 for e in store.get_events(run_id) if e.type == EventType.LLM_END)


# ---------------------------------------------------------------
# Pause / resume Run A
# ---------------------------------------------------------------
section("[2/5]  Pause Run A mid-flight", YELLOW)
worker.start()
assert started.wait(timeout=5), "worker did not start"
cp.pause(run_a_id)
time.sleep(0.6)

run_a = store.get_run(run_a_id)
llm_end_a = count_llm_end(run_a_id)
assert run_a is not None
assert run_a.status == RunStatus.PAUSED, f"expected PAUSED, got {run_a.status}"
assert worker.is_alive(), "worker should be blocked in before_llm_call"
assert llm_end_a == 0, f"expected no LLM_END while paused, got {llm_end_a}"

print(f"  {YELLOW}{BOLD}{RunStatus(run_a.status).value.upper()}{RESET}  Run A is held before its LLM call")
print(f"  {DIM}worker alive:{RESET}  {worker.is_alive()}   {DIM}LLM_END events:{RESET}  {llm_end_a}")
time.sleep(2.0)

cp.resume(run_a_id)
print(f"\n  {GREEN}{BOLD}RESUMED{RESET}  Run A continues")
worker.join(timeout=5)
assert not worker.is_alive(), "worker did not finish after resume"
store.update_run_status(run_a_id, RunStatus.COMPLETED, end_time=time.time())

run_a = store.get_run(run_a_id)
assert run_a is not None
assert run_a.status == RunStatus.COMPLETED, f"expected COMPLETED, got {run_a.status}"
assert count_llm_end(run_a_id) == 1, "expected exactly one LLM_END after resume"
cp.cleanup(run_a_id)

print(f"  {GREEN}✓ Run A complete{RESET}")

# ---------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------
section("[3/5]  Hypothesis", MAGENTA)
typed(f"  {YELLOW}A shorter system prompt will produce a concise response.{RESET}")
typed(f"  {DIM}expected_output:{RESET}  {BOLD}'concise'{RESET}")

# ---------------------------------------------------------------
# Run B: concise
# ---------------------------------------------------------------
section("[4/5]  Run B — fork with edited system prompt", YELLOW)
typed(f"  {DIM}prompt:{RESET}  Answer in one short sentence.")
typed(f"  {DIM}query:{RESET}   {QUESTION}")
print()

run_b_id = str(uuid.uuid4())
span_b_id = str(uuid.uuid4())
t_b = time.time()

store.save_run(Run(
    id=run_b_id, name="concise_agent",
    start_time=t_b, end_time=t_b + 0.820,
    status=RunStatus.COMPLETED,
    parent_run_id=run_a_id, fork_span_id=span_a_id,
    notes="Hypothesis: a shorter system prompt will produce a concise response",
    expected_output="concise",
))
store.save_span(Span(
    id=span_b_id, run_id=run_b_id, name="chat.completions", type="llm",
    start_time=t_b, end_time=t_b + 0.820,
))
store.save_event(Event(
    run_id=run_b_id, span_id=span_b_id, type=EventType.LLM_START, timestamp=t_b,
    data={"messages": [
        {"role": "system", "content": CONCISE_SYSTEM},
        {"role": "user", "content": QUESTION},
    ]},
))
store.save_event(Event(
    run_id=run_b_id, span_id=span_b_id, type=EventType.LLM_END, timestamp=t_b + 0.820,
    data={
        "latency_ms": 820, "total_tokens": 87, "cost_usd": 0.00087,
        "response": {"choices": [{"message": {"content": CONCISE_RESPONSE}}]},
    },
))

print(f"  {GREEN}✓ Run B complete{RESET}")

# ---------------------------------------------------------------
# Diff
# ---------------------------------------------------------------
section("[5/5]  GET /runs/A/diff/B", CYAN)
diff_url = f"http://localhost:7878/runs/{run_a_id}/diff/{run_b_id}"
typed(f"  {DIM}$ curl -s {diff_url[:50]}...{RESET}")
print()

for attempt in range(15):
    try:
        with urllib.request.urlopen(diff_url, timeout=3) as resp:
            diff = json.loads(resp.read())
        break
    except Exception:
        if attempt == 14:
            raise
        time.sleep(0.5)

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

resp_diff = diff["response_diff"]
print(f"\n  {BOLD}Response{RESET}")
print(f"    {DIM}A:{RESET}  {(resp_diff['a'] or '')[:90]}{DIM}...{RESET}")
print(f"    {DIM}B:{RESET}  {(resp_diff['b'] or '')[:90]}")

ar = diff["assertion_result"]
print(f"\n  {BOLD}Assertion{RESET}")
print(f"    {DIM}expected_output:{RESET}  {ar['expected_output']!r}")
if ar["passed_in_a"]:
    print(f"    {DIM}passed in A:{RESET}     {GREEN}✓ true{RESET}")
else:
    print(f"    {DIM}passed in A:{RESET}     {RED}✗ false{RESET}")
if ar["passed_in_b"]:
    print(f"    {DIM}passed in B:{RESET}     {GREEN}✓ true{RESET}")
else:
    print(f"    {DIM}passed in B:{RESET}     {RED}✗ false{RESET}")

verdict = ar["verdict"]
print()
if verdict == "improved":
    print(f"  {BG_GREEN}{WHITE}{BOLD}                                                        {RESET}")
    print(f"  {BG_GREEN}{WHITE}{BOLD}              VERDICT: HYPOTHESIS CONFIRMED            {RESET}")
    print(f'  {BG_GREEN}{WHITE}{BOLD}                  verdict: "improved"                  {RESET}')
    print(f"  {BG_GREEN}{WHITE}{BOLD}                                                        {RESET}")
else:
    print(f"  {BOLD}verdict:{RESET} {verdict}")

print(f"\n  {DIM}Full diff:  {diff_url}{RESET}")
print(f"  {DIM}Dashboard:  http://localhost:7878{RESET}\n")
print(f"  {BOLD}{MAGENTA}pip install agentlens-tracer{RESET}\n")
