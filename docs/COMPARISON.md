# Comparison

agent-lens is a framework-agnostic, local-first debugger for LLM agents — pause, fork, and get a numeric verdict on which run actually worked. This page compares that specific mechanic against the closest real alternatives, rather than doing a generic feature-list comparison. Sources below were fetched and verified 2026-09-08.

## The claim being evaluated

> Pauses a live, in-flight agent process (not a completed or checkpointed run) and forks it into two comparable runs with a numeric verdict.

Most "compare two agent runs" tooling operates on runs that have already finished, or on checkpoints written after the fact. The question that actually separates the tools below is narrower and more useful: can it interrupt a process that is still running, fork it, and tell you numerically which fork did better? That's the axis this page sticks to.

## Spine comparison

The LangGraph and Arize Phoenix rows below are sourced from each vendor's own public documentation, linked per row, and independently checkable by any reader. The agent-lens row is self-reported from agent-lens's own README — the normal thing for a project comparing itself to others, but not verified by a third party. The agent-lens row is written with the same register as the other two: it is not claimed with more certainty than the cited-and-checkable rows next to it.

| Tool | Pauses a live in-flight process? | Forks into a comparable second run? | Numeric verdict between forks? | Source |
|---|---|---|---|---|
| agent-lens (self-reported) | Yes — pauses the running agent at any LLM call | Yes — fork with edited messages while the original keeps running | Yes — diffs the two runs and returns a verdict | [agent-lens README](https://github.com/RAJUSHANIGARAPU/agent-lens) |
| LangGraph time-travel | No — works by resuming execution from a prior checkpoint, not by intercepting a currently-running process | Partial — `update_state` does not roll back a thread; it creates a new checkpoint that branches from the specified point, and the original execution history remains intact | Not built in — the docs describe no numeric verdict between the resulting branches | [LangGraph docs: time-travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel) |
| Arize Phoenix experiments | No — runs a task function against a dataset, or lets you upload already-finished results, then scores and compares | Yes, but from completed or dataset-driven executions — not from interrupting a live in-flight process | Yes — experiments are scored and compared, but on completed runs, not on live in-process forks | [Arize Phoenix docs: run experiments](https://arize.com/docs/ax/develop/datasets-and-experiments/run-experiments) |

## Langfuse and LangSmith

Langfuse and LangSmith are not evaluated here because they sit in an adjacent category — hosted trace/observability analysis for completed or streaming runs — rather than competing on the specific pause-a-live-process-and-fork mechanic this page is about.

## When not to use agent-lens

- If you need checkpoint-based replay across a long-running, multi-day workflow, LangGraph's time-travel is built for that; agent-lens's pause only holds a process that is actually still running right now.
- If your team already runs a hosted, multi-user, dataset-driven eval pipeline against completed executions and wants results in a shared team dashboard, Arize Phoenix experiments fit that directly; agent-lens is a local, single-user debugging tool with no shared dashboard.
- If you want a hosted SaaS trace-analysis product with no local infrastructure at all, Langfuse or LangSmith are that; agent-lens is local-first/SQLite by design and has no hosted offering.
