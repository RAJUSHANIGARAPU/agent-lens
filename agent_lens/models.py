"""
agent_lens.models
~~~~~~~~~~~~~~~~~
Core data models for agent-lens: Event, Span, Run and their supporting enums.
All models use Pydantic v2 for validation and serialization.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events that can occur during an agent run."""

    LLM_START = "llm_start"
    LLM_END = "llm_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    ERROR = "error"


class RunStatus(str, Enum):
    """Possible states for an agent run."""

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    FORKED = "forked"


class Event(BaseModel):
    """A single discrete event captured during an agent run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    span_id: str
    parent_span_id: str | None = None
    type: EventType
    timestamp: float = Field(default_factory=time.time)
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class Span(BaseModel):
    """A logical unit of work within an agent run (an LLM call, a tool call, etc.)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    parent_id: str | None = None
    name: str
    type: str  # "llm", "tool", "agent", "chain"
    start_time: float = Field(default_factory=time.time)
    end_time: float | None = None
    events: list[Event] = Field(default_factory=list)
    children: list[Span] = Field(default_factory=list)
    status: str = "ok"  # "ok", "error", "paused"

    @property
    def duration_ms(self) -> float | None:
        """Return span duration in milliseconds, or None if still open."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    model_config = {"use_enum_values": True}


class Run(BaseModel):
    """An agent run — the top-level container for all spans and events."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    start_time: float = Field(default_factory=time.time)
    end_time: float | None = None
    status: RunStatus = RunStatus.RUNNING
    root_span: Span | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None  # Set when this run is a fork
    fork_span_id: str | None = None  # The span from which this run was forked
    notes: str | None = None  # Developer annotation: why this fork was created
    expected_output: str | None = None  # Assertion: what success looks like for this fork

    @property
    def duration_ms(self) -> float | None:
        """Return run duration in milliseconds, or None if still running."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    @property
    def is_fork(self) -> bool:
        """Return True if this run is a fork of another run."""
        return self.parent_run_id is not None

    model_config = {"use_enum_values": True}


# Rebuild model to resolve forward references
Span.model_rebuild()
