"""
agent_lens.dashboard_launcher
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Starts the agent-lens dashboard server in a background daemon thread
and optionally opens a browser tab.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import Any

_server_thread: threading.Thread | None = None
_server_url: str | None = None
_server_started = threading.Event()


def start(
    port: int = 7878,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    store: Any | None = None,
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

    Returns
    -------
    str
        The URL at which the dashboard is running.
    """
    global _server_thread, _server_url, _server_started

    url = f"http://{host}:{port}"

    if _server_thread is not None and _server_thread.is_alive():
        return _server_url or url

    _server_started.clear()
    _server_url = url

    from agent_lens.server import CSRF_TOKEN, create_app
    print(f"agent-lens CSRF token: {CSRF_TOKEN}")

    app = create_app(store=store)

    def _run_server():
        import uvicorn

        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
            loop="asyncio",
        )
        server = uvicorn.Server(config)

        # Signal that the server is about to start
        _server_started.set()
        server.run()

    _server_thread = threading.Thread(target=_run_server, name="agent-lens-server", daemon=True)
    _server_thread.start()

    # Wait for server to start (up to 5 seconds)
    _server_started.wait(timeout=5.0)
    # Give uvicorn a moment to bind the port
    time.sleep(0.5)

    print(f"agent-lens dashboard running at {url}")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # Non-fatal if browser can't be opened

    return url


def is_running() -> bool:
    """Return True if the dashboard server thread is alive."""
    return _server_thread is not None and _server_thread.is_alive()


def get_url() -> str | None:
    """Return the current dashboard URL, or None if not started."""
    return _server_url
