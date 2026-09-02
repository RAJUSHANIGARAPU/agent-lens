"""
Fixtures for the provider-integration tests.

The integrations monkey-patch classes inside the vendor SDKs, so exercising them
normally means installing openai / anthropic / langchain / llama-index into the
test environment. That is heavy, slow on a 3-OS x 3-Python matrix, and couples
the suite to vendor release cadence — which is why these modules sat at 0%
coverage while a crash lived in one of them.

Instead we install a *stub* SDK into ``sys.modules`` that mirrors the real
import paths and class names, and let ``patch()`` bind to classes we control.
The wrapper logic under test is agent-lens code; the SDK only has to be shaped
like the real one. The shape itself is pinned by
``test_sdk_surface.py::test_stub_matches_real_sdk_surface``, so the stubs cannot
silently drift away from the vendors.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

# In the CI job that installs the real vendor SDKs there is no legitimate reason
# for a surface check to skip, and a skip there is worse than a failure: a failing
# test is a signal, a skipped one is the *absence* of a signal, and from outside
# both look like a green build. That is how the OpenAI crash survived four months
# — the suite skipped whenever the SDK was missing, which it always was.
#
# So in that job, skipping is promoted to failing. Deliberately implemented as a
# report hook rather than by hardening `importorskip`, because it has to catch a
# skip from *any* cause, not just the one we happen to have thought of.
REQUIRE_SDKS = os.environ.get("AGENT_LENS_REQUIRE_SDKS") == "1"

_NO_SKIP_MODULES = ("test_sdk_surface",)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield

    if (
        REQUIRE_SDKS
        and report.skipped
        and any(name in str(item.fspath) for name in _NO_SKIP_MODULES)
    ):
        reason = ""
        if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
            reason = report.longrepr[2]
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} SKIPPED while AGENT_LENS_REQUIRE_SDKS=1.\n"
            f"Reason given: {reason or '<none>'}\n\n"
            "This job exists to check the integrations against the real vendor SDKs. "
            "A skip here means that check did not happen, and a run that verifies "
            "nothing must not report success."
        )

    return report


def _make_package(name: str) -> types.ModuleType:
    """Create an empty module that behaves like a package."""
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = []  # marks it as a package for the import machinery
    return module


def _install(monkeypatch, tree: dict[str, types.ModuleType]) -> None:
    """
    Register a module tree in sys.modules and wire each child onto its parent.

    Both are needed: ``from a.b import c`` consults sys.modules for ``a.b`` and
    then getattrs ``c`` off it.
    """
    for dotted, module in tree.items():
        monkeypatch.setitem(sys.modules, dotted, module)
        if "." in dotted:
            parent_name, _, child = dotted.rpartition(".")
            monkeypatch.setattr(tree[parent_name], child, module, raising=False)


class StubUsage:
    """Mirrors the ``response.usage`` object both SDKs expose."""

    def __init__(self, **fields: int) -> None:
        self._fields = dict(fields)
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self) -> dict:
        return dict(self._fields)


class StubThinkingBlock:
    def __init__(self, thinking: str) -> None:
        self.type = "thinking"
        self.thinking = thinking

    def model_dump(self) -> dict:
        return {"type": self.type, "thinking": self.thinking}


class StubResponse:
    """
    A minimal SDK response.

    Both vendors return pydantic models, so ``model_dump()`` is the path
    ``_safe_response_dict`` actually takes in production and the stub must
    expose it — reading ``__dict__`` instead would embed live objects that no
    JSON encoder can persist, which is a property of the stub rather than of
    any real response.
    """

    def __init__(self, usage: StubUsage | None = None, content: list | None = None) -> None:
        self.usage = usage
        self.content = content or []

    def model_dump(self) -> dict:
        return {
            "usage": self.usage.model_dump() if self.usage else None,
            "content": [block.model_dump() for block in self.content],
        }


class StubClient:
    """
    Base for the stub SDK clients.

    Records the kwargs each call received so a test can prove the wrapper
    delegated unchanged, and lets a test stage a response or an exception:

        client.response = StubResponse(...)   # what create() returns
        client.raises = RuntimeError("boom")  # what create() raises instead
    """

    default_usage: dict[str, int] = {}

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response: StubResponse | None = None
        self.raises: Exception | None = None

    def _respond(self, kwargs: dict):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        if self.response is not None:
            return self.response
        return StubResponse(StubUsage(**self.default_usage))


# ----------------------------------------------------------------------
# openai
# ----------------------------------------------------------------------


@pytest.fixture
def fake_openai(monkeypatch):
    """
    Install a stub ``openai`` package and return its completions module.

    The returned module carries ``Completions`` and ``AsyncCompletions``, the two
    classes the integration patches. Each records the calls its original method
    received, so a test can prove the wrapper delegated.
    """

    class Completions(StubClient):
        default_usage = {"prompt_tokens": 10, "completion_tokens": 5}

        def create(self, **kwargs):
            return self._respond(kwargs)

    class AsyncCompletions(StubClient):
        default_usage = {"prompt_tokens": 10, "completion_tokens": 5}

        async def create(self, **kwargs):
            return self._respond(kwargs)

    openai_mod = _make_package("openai")
    openai_mod.__version__ = "stub"
    resources = _make_package("openai.resources")
    chat = _make_package("openai.resources.chat")
    completions = _make_package("openai.resources.chat.completions")
    completions.Completions = Completions
    completions.AsyncCompletions = AsyncCompletions

    _install(
        monkeypatch,
        {
            "openai": openai_mod,
            "openai.resources": resources,
            "openai.resources.chat": chat,
            "openai.resources.chat.completions": completions,
        },
    )
    return completions


# ----------------------------------------------------------------------
# anthropic
# ----------------------------------------------------------------------


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Install a stub ``anthropic`` package and return its messages module."""

    class Messages(StubClient):
        default_usage = {"input_tokens": 100, "output_tokens": 50}

        def create(self, **kwargs):
            return self._respond(kwargs)

    class AsyncMessages(StubClient):
        default_usage = {"input_tokens": 100, "output_tokens": 50}

        async def create(self, **kwargs):
            return self._respond(kwargs)

    anthropic_mod = _make_package("anthropic")
    anthropic_mod.__version__ = "stub"
    resources = _make_package("anthropic.resources")
    messages = _make_package("anthropic.resources.messages")
    messages.Messages = Messages
    messages.AsyncMessages = AsyncMessages

    _install(
        monkeypatch,
        {
            "anthropic": anthropic_mod,
            "anthropic.resources": resources,
            "anthropic.resources.messages": messages,
        },
    )
    return messages


# ----------------------------------------------------------------------
# patch-state hygiene
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_patch_state():
    """
    Guarantee every test starts and ends unpatched.

    The integrations keep module-level patch state, so a test that patches and
    does not restore would leak wrappers into every later test in the session.
    """
    from agent_lens.integrations import anthropic as anthropic_integration
    from agent_lens.integrations import openai as openai_integration

    for module in (openai_integration, anthropic_integration):
        module.unpatch()

    yield

    for module in (openai_integration, anthropic_integration):
        module.unpatch()


@pytest.fixture
def sdk_absent(monkeypatch):
    """
    Return a callable that makes a vendor SDK unimportable for one test.

    Asserting graceful degradation by simply *not* installing the SDK ties the
    test to the environment, so it passes on the offline matrix and fails in any
    job that does install the vendors. Binding ``None`` into sys.modules makes
    ``import <name>`` raise ImportError regardless of what is on disk.
    """

    def _hide(*names: str) -> None:
        for name in names:
            for dotted in list(sys.modules):
                if dotted == name or dotted.startswith(f"{name}."):
                    monkeypatch.delitem(sys.modules, dotted, raising=False)
            monkeypatch.setitem(sys.modules, name, None)

    return _hide


@pytest.fixture
def llm_events(reset_singletons):
    """
    Return a callable giving the provider events recorded so far.

    ``Tracer.start_span`` emits its own LLM_START bookkeeping event, so tests
    filter on the ``provider`` key that only the integrations set.
    """
    store = reset_singletons

    def _events(event_type: str | None = None) -> list:
        collected = []
        for run in store.get_runs(limit=50):
            for event in store.get_events(run.id):
                if not isinstance(event.data, dict):
                    continue
                if "provider" not in event.data:
                    continue
                if event_type is not None and event.type != event_type:
                    continue
                collected.append(event)
        return collected

    return _events
