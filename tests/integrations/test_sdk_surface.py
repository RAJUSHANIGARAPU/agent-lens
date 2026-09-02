"""
Vendor SDK surface.

The rest of the integration suite runs against stubs, which proves the wrapper
logic but cannot prove the wrapper is bolted to attributes the vendors actually
ship. That gap is exactly how the OpenAI integration came to patch
``Completions.acreate`` — an attribute no openai >=1.0 has ever defined — and
crash ``agent_lens.install()`` for every real user.

The callback integrations have the same seam in a different shape. They do not
patch anything; they subclass a vendor base class and override the methods the
framework calls. If a vendor renames one, the override is simply never invoked —
no error, no span, no trace. That is the identical failure mode as the async bug,
and equally invisible to a stub, because a stub base class accepts any override
name we care to define.

These tests close both. They skip when the vendor is absent, so the default
offline run is unaffected, and they fail loudly in the job that installs them.
"""

from __future__ import annotations

import inspect

import pytest

from agent_lens.integrations import anthropic as anthropic_integration
from agent_lens.integrations import openai as openai_integration


@pytest.fixture(autouse=True)
def restore_real_sdk():
    """Never leave a patched vendor class behind for the rest of the session."""
    yield
    openai_integration.unpatch()
    anthropic_integration.unpatch()


class TestOpenAISurface:
    @pytest.fixture
    def completions(self):
        pytest.importorskip("openai", reason="openai SDK not installed in this job")
        from openai.resources.chat import completions

        return completions

    def test_sync_call_site_exists(self, completions):
        assert hasattr(completions, "Completions")
        assert callable(getattr(completions.Completions, "create", None))

    def test_async_call_site_exists(self, completions):
        assert hasattr(completions, "AsyncCompletions")
        assert callable(getattr(completions.AsyncCompletions, "create", None))

    def test_integration_does_not_depend_on_acreate(self, completions):
        """
        ``Completions.acreate`` does not exist on any supported SDK. If a future
        release introduces it, revisit the integration deliberately rather than
        letting the async path silently bind to a second call site.
        """
        assert not hasattr(completions.Completions, "acreate")

    def test_patch_succeeds_against_the_real_sdk(self, completions):
        assert openai_integration.patch() is True


class TestAnthropicSurface:
    @pytest.fixture
    def messages(self):
        pytest.importorskip("anthropic", reason="anthropic SDK not installed in this job")
        from anthropic.resources import messages

        return messages

    def test_sync_call_site_exists(self, messages):
        assert hasattr(messages, "Messages")
        assert callable(getattr(messages.Messages, "create", None))

    def test_async_call_site_exists(self, messages):
        assert hasattr(messages, "AsyncMessages")
        assert callable(getattr(messages.AsyncMessages, "create", None))

    def test_integration_does_not_depend_on_acreate(self, messages):
        assert not hasattr(messages.Messages, "acreate")

    def test_patch_succeeds_against_the_real_sdk(self, messages):
        assert anthropic_integration.patch() is True


def _overridden_hooks(handler_cls, prefixes: tuple[str, ...]) -> set[str]:
    """
    Names the handler defines that the framework is expected to call.

    Derived from the class rather than hard-coded, so a hook added later is
    checked against the vendor automatically instead of being forgotten.
    """
    return {
        name
        for name, _ in inspect.getmembers(handler_cls, inspect.isfunction)
        if name.startswith(prefixes) and name in vars(handler_cls)
    }


class TestLangChainSurface:
    @pytest.fixture
    def base(self):
        pytest.importorskip("langchain_core", reason="langchain-core not installed in this job")
        from langchain_core.callbacks.base import BaseCallbackHandler

        return BaseCallbackHandler

    def test_the_real_base_class_was_imported(self, base):
        """
        The integration falls back to plain `object` when the import fails. That
        fallback is correct for "not installed" and catastrophic for "renamed":
        the handler would still construct and still register, and simply never
        record anything.
        """
        from agent_lens.integrations import langchain as integration

        assert integration._LANGCHAIN_AVAILABLE is True
        assert integration.BaseCallbackHandler is base

    def test_every_overridden_hook_exists_on_the_vendor_base(self, base):
        from agent_lens.integrations.langchain import AgentLensCallbackHandler

        hooks = _overridden_hooks(AgentLensCallbackHandler, ("on_",))
        assert hooks, "no hooks discovered — the introspection is broken, not the handler"

        missing = sorted(h for h in hooks if not hasattr(base, h))
        assert not missing, f"overrides the framework will never call: {missing}"

    def test_handler_instantiates_against_the_real_base(self, base):
        from agent_lens.integrations.langchain import AgentLensCallbackHandler

        assert isinstance(AgentLensCallbackHandler(), base)


class TestLlamaIndexSurface:
    @pytest.fixture
    def base(self):
        pytest.importorskip("llama_index.core", reason="llama-index-core not installed in this job")
        from llama_index.core.callbacks.base_handler import BaseCallbackHandler

        return BaseCallbackHandler

    def test_the_real_base_class_was_imported(self, base):
        from agent_lens.integrations import llamaindex as integration

        assert integration._LLAMAINDEX_AVAILABLE is True
        assert integration.BaseCallbackHandler is base

    def test_event_schema_symbols_are_importable(self, base):
        from agent_lens.integrations import llamaindex as integration

        assert integration.CBEventType is not None
        assert integration.EventPayload is not None

    def test_every_overridden_hook_exists_on_the_vendor_base(self, base):
        from agent_lens.integrations.llamaindex import AgentLensLlamaIndexHandler

        hooks = _overridden_hooks(
            AgentLensLlamaIndexHandler, ("on_", "start_trace", "end_trace")
        )
        assert hooks, "no hooks discovered — the introspection is broken, not the handler"

        missing = sorted(h for h in hooks if not hasattr(base, h))
        assert not missing, f"overrides the framework will never call: {missing}"

    def test_handler_instantiates_against_the_real_base(self, base):
        """
        The vendor base takes two required arguments; ours takes none and has to
        supply them. A stub base class hides that entirely.
        """
        from agent_lens.integrations.llamaindex import AgentLensLlamaIndexHandler

        handler = AgentLensLlamaIndexHandler()

        assert isinstance(handler, base)
        assert handler.event_starts_to_ignore is not None
        assert handler.event_ends_to_ignore is not None


class TestInstallEntryPoint:
    def test_install_never_raises(self):
        """
        ``agent_lens.install()`` is the package's documented one-liner. Whatever
        combination of SDKs is present, it must return a result map rather than
        raise — the failure mode that shipped in 0.1.0 and 0.2.0.
        """
        import agent_lens

        results = agent_lens.install()

        assert set(results) == {"openai", "anthropic"}
        assert all(isinstance(v, bool) for v in results.values())
