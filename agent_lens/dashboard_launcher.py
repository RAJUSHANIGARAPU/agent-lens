"""
agent_lens.dashboard_launcher
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Starts the agent-lens dashboard server in a background daemon thread
and optionally opens a browser tab.
"""

from __future__ import annotations

import contextlib
import threading
import time
import webbrowser
from typing import Any

_server_thread: threading.Thread | None = None
_server_url: str | None = None
_server: Any | None = None


class DashboardStartError(RuntimeError):
    """Raised when the dashboard server could not start (e.g. the port is in use)."""


def start(
    port: int = 7878,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    store: Any | None = None,
    startup_timeout: float = 5.0,
) -> str:
    """
    Start the agent-lens dashboard in a background daemon thread.

    Parameters
    ----------
    port : int
        TCP port to bind to (default 7878).
    host : str
        Host to bind to. Defaults to 127.0.0.1 (loopback only).
    open_browser : bool
        If True, opens the dashboard in the default browser after startup.
    store : Store | None
        Custom Store instance (for testing). Uses the default store if None.
    startup_timeout : float
        Seconds to wait for the server to start accepting connections.

    Returns
    -------
    str
        The URL at which the dashboard is running.

    Raises
    ------
    DashboardStartError
        If the server did not come up, most commonly because the port is
        already in use.
    """
    global _server_thread, _server_url, _server

    url = f"http://{host}:{port}"

    if _server_thread is not None and _server_thread.is_alive():
        return _server_url or url

    import uvicorn

    from agent_lens.server import CSRF_TOKEN, create_app

    app = create_app(store=store)
    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
            loop="asyncio",
        )
    )

    # uvicorn reports a bind failure by logging it and calling sys.exit(1),
    # which in a background thread only ends that thread. So "started" is
    # judged by uvicorn's own flag, not by the thread having been launched.
    thread = threading.Thread(target=server.run, name="agent-lens-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)

    if not server.started:
        server.should_exit = True
        if thread.is_alive():
            reason = f"the server did not start within {startup_timeout:g}s"
        else:
            reason = f"the server exited during startup; port {port} is probably already in use"
        raise DashboardStartError(
            f"agent-lens dashboard failed to start on {url}: {reason}. "
            f"Free the port or choose another one, e.g. start(port={port + 1})."
        )

    _server = server
    _server_thread = thread
    _server_url = url

    print(f"agent-lens CSRF token: {CSRF_TOKEN}")
    print(f"agent-lens dashboard running at {url}")

    if open_browser:
        with contextlib.suppress(Exception):  # Non-fatal if browser can't be opened
            webbrowser.open(url)

    return url


def is_running() -> bool:
    """Return True if the dashboard server thread is alive."""
    return _server_thread is not None and _server_thread.is_alive()


def get_url() -> str | None:
    """Return the current dashboard URL, or None if not started."""
    return _server_url
