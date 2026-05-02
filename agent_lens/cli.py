"""
agent_lens.cli
~~~~~~~~~~~~~~
Typer CLI for agent-lens.

Commands:
    agent-lens dashboard [--port 7878]
    agent-lens replay <file.agentlens>
    agent-lens export <run_id> [--output file.html]
    agent-lens version
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="agent-lens",
    help="The interactive debugger for LLM agents.",
    add_completion=False,
)


@app.command()
def dashboard(
    port: int = typer.Option(7878, "--port", "-p", help="Port to bind to"),
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Host to bind to"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
) -> None:
    """Start the agent-lens dashboard server."""
    from agent_lens.dashboard_launcher import start

    typer.echo(f"Starting agent-lens dashboard on http://{host}:{port}")
    start(port=port, host=host, open_browser=not no_browser)

    typer.echo("Press Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        typer.echo("\nShutting down.")
        raise typer.Exit(0) from None


@app.command()
def replay(
    file_path: str = typer.Argument(..., help="Path to a .agentlens file to load"),
    port: int = typer.Option(7878, "--port", "-p", help="Port to bind to"),
) -> None:
    """Load a saved .agentlens run file into the dashboard."""
    # Security: validate path to prevent path traversal
    resolved = Path(file_path).resolve()

    # Reject paths that look like system files
    forbidden_prefixes = ["/etc", "/proc", "/sys", "/dev", "/root"]
    if sys.platform != "win32":
        for prefix in forbidden_prefixes:
            if str(resolved).startswith(prefix):
                typer.echo(f"Error: Access to {prefix!r} is not allowed.", err=True)
                raise typer.Exit(1)

    if not resolved.exists():
        typer.echo(f"Error: File not found: {file_path!r}", err=True)
        raise typer.Exit(1)

    if not resolved.is_file():
        typer.echo(f"Error: Not a file: {file_path!r}", err=True)
        raise typer.Exit(1)

    # Only allow .agentlens or .json files
    if resolved.suffix not in (".agentlens", ".json"):
        typer.echo(
            f"Error: Only .agentlens and .json files are supported. Got: {resolved.suffix!r}",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Loading {resolved} ...")

    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        typer.echo(f"Error reading file: {exc}", err=True)
        raise typer.Exit(1) from None

    # Import run data into a temporary in-memory store
    import tempfile

    from agent_lens.store import Store

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as _f:
        tmp_name = _f.name
    store = Store(path=tmp_name)

    from agent_lens.models import Event, Run, Span

    try:
        if "run" in data:
            run_data = data["run"]
            run = Run(**run_data)
            store.save_run(run)

        for span_data in data.get("spans", []):
            span = Span(**{k: v for k, v in span_data.items() if k in Span.model_fields})
            store.save_span(span)

        for event_data in data.get("events", []):
            event = Event(**{k: v for k, v in event_data.items() if k in Event.model_fields})
            store.save_event(event)
    except Exception as exc:
        typer.echo(f"Error importing run data: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo("Run loaded. Starting dashboard...")

    from agent_lens.dashboard_launcher import start
    start(port=port, host="127.0.0.1", open_browser=True, store=store)

    typer.echo(f"Dashboard at http://127.0.0.1:{port}")
    typer.echo("Press Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        os.unlink(tmp_name)
        raise typer.Exit(0) from None


@app.command()
def export(
    run_id: str = typer.Argument(..., help="Run ID to export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
) -> None:
    """Export a run as a self-contained HTML file."""
    from agent_lens.store import get_default_store

    store = get_default_store()
    run = store.get_run(run_id)
    if run is None:
        typer.echo(f"Error: Run {run_id!r} not found.", err=True)
        raise typer.Exit(1)

    import html as html_module
    import json as json_module
    from pathlib import Path as PathLib

    spans = store.get_spans(run_id)
    events = store.get_events(run_id)

    data = {
        "run": run.model_dump(),
        "spans": [s.model_dump() for s in spans],
        "events": [e.model_dump() for e in events],
    }

    json_data = json_module.dumps(data, default=str)

    dashboard_dir = PathLib(__file__).parent / "dashboard"
    css_path = dashboard_dir / "style.css"
    js_path = dashboard_dir / "app.js"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    js = js_path.read_text(encoding="utf-8") if js_path.exists() else ""

    export_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>agent-lens export: {html_module.escape(run.name)}</title>
<style>{css}</style>
</head>
<body>
<script>
window.__AGENT_LENS_EXPORT__ = true;
window.__AGENT_LENS_DATA__ = JSON.parse(document.getElementById('al-data').textContent);
</script>
<script id="al-data" type="application/json">{json_data}</script>
<script>{js}</script>
</body>
</html>"""

    out_path = output or f"run-{run_id[:8]}.html"
    Path(out_path).write_text(export_html, encoding="utf-8")
    typer.echo(f"Exported to {out_path}")


@app.command()
def version() -> None:
    """Print the agent-lens version."""
    try:
        from importlib.metadata import version as pkg_version
        v = pkg_version("agent-lens")
    except Exception:
        v = "0.1.0"
    typer.echo(f"agent-lens {v}")


if __name__ == "__main__":
    app()
