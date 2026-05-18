"""
agent-lens
~~~~~~~~~~
The interactive debugger for LLM agents.

Pause a running agent, edit its memory or messages, and fork a new run
from any point in time — with zero infrastructure.

Quick start::

    import agent_lens

    agent_lens.install()         # auto-patch OpenAI + Anthropic SDKs
    agent_lens.dashboard.start() # open the local dashboard at localhost:7878

    @agent_lens.trace
    def my_agent(query: str) -> str:
        ...
"""

from __future__ import annotations

from agent_lens import dashboard_launcher as dashboard
from agent_lens.models import Event, EventType, Run, RunStatus, Span
from agent_lens.tracer import Tracer, trace, trace_span  # noqa: F401

__version__ = "0.2.0"
__author__ = "Raju S"
__email__ = "rajub4u927@gmail.com"

__all__ = [
    # Core decorator
    "trace",
    "trace_span",
    # Auto-install
    "install",
    # Dashboard module
    "dashboard",
    # Models (for type hints)
    "Event",
    "EventType",
    "Run",
    "RunStatus",
    "Span",
]


def install(
    openai: bool = True,
    anthropic: bool = True,
) -> dict[str, bool]:
    """
    Automatically patch installed LLM SDKs so all calls are traced.

    This is the "zero code change" path: call once at startup and all
    subsequent OpenAI / Anthropic calls are captured automatically.

    Parameters
    ----------
    openai : bool
        If True (default), patch the OpenAI SDK.
    anthropic : bool
        If True (default), patch the Anthropic SDK.

    Returns
    -------
    dict[str, bool]
        Mapping of SDK name → whether patching succeeded.
        A value of False means the SDK is not installed (not an error).
    """
    results: dict[str, bool] = {}

    if openai:
        from agent_lens.integrations import openai as _openai_integration
        results["openai"] = _openai_integration.patch()

    if anthropic:
        from agent_lens.integrations import anthropic as _anthropic_integration
        results["anthropic"] = _anthropic_integration.patch()

    return results
