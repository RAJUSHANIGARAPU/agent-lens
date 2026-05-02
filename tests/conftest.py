"""
Shared pytest fixtures for agent-lens tests.
Each test gets a fresh in-memory SQLite store and reset singletons.
"""

import os
import tempfile
from pathlib import Path

import pytest

from agent_lens.control import ControlPlane
from agent_lens.store import Store, reset_default_store
from agent_lens.tracer import EventBus, Tracer, TraceContext


@pytest.fixture(autouse=True)
def reset_singletons(tmp_path):
    """
    Reset all module-level singletons before each test.
    This ensures test isolation.
    """
    # Reset all singletons
    Tracer.reset()
    ControlPlane.reset()
    EventBus.reset()
    TraceContext.clear()

    # Create a fresh in-memory store for each test
    db_path = tmp_path / "test.db"
    store = reset_default_store(path=db_path)

    yield store

    # Cleanup
    TraceContext.clear()
    Tracer.reset()
    ControlPlane.reset()
    EventBus.reset()


@pytest.fixture
def store(tmp_path):
    """Return a fresh Store backed by a temp SQLite file."""
    db_path = tmp_path / "test.db"
    s = Store(path=db_path)
    yield s
    s.close()


@pytest.fixture
def control():
    """Return a fresh ControlPlane."""
    cp = ControlPlane.get_instance()
    return cp
