"""Tests for agent_lens.dashboard_launcher startup reporting."""

from __future__ import annotations

import socket
import sys
import urllib.request

import pytest

from agent_lens import dashboard_launcher
from agent_lens.dashboard_launcher import DashboardStartError


@pytest.fixture
def launcher(reset_singletons):
    """Run each test against a clean launcher and stop any server it started."""
    dashboard_launcher._server = None
    dashboard_launcher._server_thread = None
    dashboard_launcher._server_url = None
    yield reset_singletons
    server, thread = dashboard_launcher._server, dashboard_launcher._server_thread
    if server is not None:
        server.should_exit = True
    if thread is not None:
        thread.join(timeout=5)
    dashboard_launcher._server = None
    dashboard_launcher._server_thread = None
    dashboard_launcher._server_url = None


@pytest.fixture
def occupied_port():
    """A loopback port held open by a listening socket for the test's duration."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        # Without this, Windows lets a second socket bind the same port.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_raises_when_port_is_in_use(launcher, occupied_port, capsys):
    with pytest.raises(DashboardStartError, match=f"port {occupied_port} is probably already in use"):
        dashboard_launcher.start(port=occupied_port, open_browser=False, store=launcher)

    assert "running" not in capsys.readouterr().out
    assert not dashboard_launcher.is_running()
    assert dashboard_launcher.get_url() is None


def test_start_reports_running_only_once_serving(launcher, capsys):
    port = _free_port()

    url = dashboard_launcher.start(port=port, open_browser=False, store=launcher)

    assert url == f"http://127.0.0.1:{port}"
    assert f"agent-lens dashboard running at {url}" in capsys.readouterr().out
    assert dashboard_launcher.is_running()
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - loopback test server
        assert resp.status == 200


def test_failed_start_can_be_retried_on_another_port(launcher, occupied_port):
    with pytest.raises(DashboardStartError):
        dashboard_launcher.start(port=occupied_port, open_browser=False, store=launcher)

    port = _free_port()
    assert dashboard_launcher.start(port=port, open_browser=False, store=launcher) == f"http://127.0.0.1:{port}"
    assert dashboard_launcher.is_running()
