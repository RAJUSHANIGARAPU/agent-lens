"""
Vendor SDK surface.

The rest of the integration suite runs against stubs, which proves the wrapper
logic but cannot prove the wrapper is bolted to attributes the vendors actually
ship. That gap is exactly how the OpenAI integration came to patch
``Completions.acreate`` — an attribute no openai >=1.0 has ever defined — and
crash ``agent_lens.install()`` for every real user.

These tests close it. They skip when the SDK is absent, so the default offline
run is unaffected, and they fail loudly in any job that installs the vendors.
"""

from __future__ import annotations

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
