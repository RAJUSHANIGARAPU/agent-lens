"""
agent_lens.mcp_server
~~~~~~~~~~~~~~~~~~~~~~~
Model Context Protocol server exposing agent-lens run history so a coding
agent can search it in-loop while developing.

Three tools, each a thin wrapper over the reusable store/export APIs:

* ``search_runs`` — full-text search over run records, hits enriched with the
  developer notes, expected_output assertion and its pass/fail result.
* ``get_run_context`` — the rich provider-neutral document for a run (the
  flattened searchable text plus structured outcome labels).
* ``get_lineage`` — the fork ancestry chain for a run, oldest ancestor first.

The tool functions are plain module-level callables (``search_runs_tool`` etc.)
so they can be imported and unit-tested without a live MCP transport. The
``FastMCP`` instance simply registers them and is run over stdio by
``agent-lens mcp``.
"""

from __future__ import annotations

from typing import Any

from agent_lens.export import assertion_passed, ctx_document
from agent_lens.store import Store, get_default_store

# Cap the number of hits a single search call can return, matching the
# dashboard endpoint's ceiling so an agent can't request an unbounded page.
_MAX_LIMIT = 50


def _store(store: Store | None) -> Store:
    """Resolve the store to use, defaulting to the shared on-disk store."""
    return store if store is not None else get_default_store()


def search_runs_tool(
    query: str,
    status: str | None = None,
    limit: int = _MAX_LIMIT,
    store: Store | None = None,
) -> list[dict[str, Any]]:
    """Search past agent runs and return ranked, outcome-labelled hits.

    Each hit carries the run id, name, status, relevance score and a match
    snippet, plus the developer ``notes``, the ``expected_output`` assertion
    and whether that assertion passed (omitted when the run has none).
    """
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(limit, _MAX_LIMIT))

    st = _store(store)
    hits = st.search_runs(query, status=status, limit=limit)
    results: list[dict[str, Any]] = []
    for hit in hits:
        entry: dict[str, Any] = {
            "run_id": hit["run_id"],
            "name": hit["name"],
            "status": hit["status"],
            "score": hit["score"],
            "snippet": hit["snippet"],
        }
        run = st.get_run(hit["run_id"])
        if run is not None:
            entry["notes"] = run.notes
            entry["expected_output"] = run.expected_output
            entry["is_fork"] = run.is_fork
            assertion = assertion_passed(run, st.get_events(run.id))
            if assertion is not None:
                entry["assertion_passed"] = assertion
        results.append(entry)
    return results


def get_run_context_tool(run_id: str, store: Store | None = None) -> dict[str, Any]:
    """Return the rich why+outcome document for a single run.

    Raises ``ValueError`` if the run does not exist.
    """
    st = _store(store)
    run = st.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id!r} not found")
    return ctx_document(st, run)


def get_lineage_tool(run_id: str, store: Store | None = None) -> dict[str, Any]:
    """Return the fork ancestry chain for a run, oldest ancestor first.

    Walks ``parent_run_id`` upward collecting each generation's summary and the
    developer notes/hypothesis, so the evolution of thinking across forks is
    visible. Raises ``ValueError`` if the run does not exist.
    """
    st = _store(store)
    run = st.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id!r} not found")

    chain: list[dict[str, Any]] = []
    current = run
    while current is not None:
        chain.append({
            "id": current.id,
            "name": current.name,
            "notes": current.notes,
            "expected_output": current.expected_output,
            "status": current.status,
            "start_time": current.start_time,
            "fork_span_id": current.fork_span_id,
            "depth": len(chain),
        })
        current = st.get_run(current.parent_run_id) if current.parent_run_id else None

    chain.reverse()  # oldest ancestor first
    return {"run_id": run_id, "lineage": chain, "depth": len(chain)}


def build_server():  # pragma: no cover - exercised only via the live transport
    """Construct the FastMCP server with the three tools registered.

    Imported lazily so the ``mcp`` dependency is only required when actually
    launching the server, not when importing the tool functions for tests.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("agent-lens")

    @mcp.tool()
    def search_runs(query: str, status: str | None = None, limit: int = _MAX_LIMIT) -> list[dict]:
        """Search past agent runs by free text; returns ranked, labelled hits."""
        return search_runs_tool(query, status=status, limit=limit)

    @mcp.tool()
    def get_run_context(run_id: str) -> dict:
        """Return the rich why+outcome document for a single run."""
        return get_run_context_tool(run_id)

    @mcp.tool()
    def get_lineage(run_id: str) -> dict:
        """Return the fork ancestry chain for a run, oldest ancestor first."""
        return get_lineage_tool(run_id)

    return mcp


def main() -> None:  # pragma: no cover - process entry point
    """Run the MCP server over stdio."""
    build_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
