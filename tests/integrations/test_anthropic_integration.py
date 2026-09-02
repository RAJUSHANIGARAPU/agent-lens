"""
Anthropic capture layer.

Exercised against a stub SDK installed into sys.modules (see conftest), so these
run offline with no vendor dependency. The stub's shape is pinned against the
real SDK by test_sdk_surface.py.
"""

from __future__ import annotations

import pytest

from agent_lens.integrations import anthropic as integration

from .conftest import StubResponse, StubThinkingBlock, StubUsage


def _llm_start(events):
    return [e for e in events if e.data.get("provider") == "anthropic" and "messages" in e.data]


def _llm_end(events):
    return [e for e in events if e.data.get("provider") == "anthropic" and "latency_ms" in e.data]


class TestPatchLifecycle:
    def test_patch_returns_false_when_sdk_absent(self, sdk_absent):
        sdk_absent("anthropic")

        assert integration.patch() is False

    def test_patch_wraps_both_sync_and_async_call_sites(self, fake_anthropic):
        original_sync = fake_anthropic.Messages.create
        original_async = fake_anthropic.AsyncMessages.create

        assert integration.patch() is True

        assert fake_anthropic.Messages.create is not original_sync
        assert fake_anthropic.AsyncMessages.create is not original_async

    def test_missing_async_class_is_skipped_not_fatal(self, fake_anthropic):
        del fake_anthropic.AsyncMessages

        assert integration.patch() is True

    def test_patch_is_idempotent(self, fake_anthropic):
        assert integration.patch() is True
        wrapper = fake_anthropic.Messages.create

        assert integration.patch() is True
        assert fake_anthropic.Messages.create is wrapper

    def test_call_site_without_the_method_is_skipped(self, fake_anthropic):
        del fake_anthropic.Messages.create

        assert integration.patch() is True
        assert not hasattr(fake_anthropic.Messages, "create")

    def test_unpatch_restores_the_original_callable(self, fake_anthropic):
        original = fake_anthropic.Messages.create

        integration.patch()
        integration.unpatch()

        assert fake_anthropic.Messages.create is original

    def test_patch_unpatch_cycle_does_not_nest_wrappers(self, fake_anthropic, llm_events):
        for _ in range(3):
            integration.patch()
            integration.unpatch()
        integration.patch()

        fake_anthropic.Messages().create(model="claude-3-5-sonnet", messages=[])

        assert len(_llm_end(llm_events())) == 1


class TestSyncCapture:
    def test_records_start_and_end_events(self, fake_anthropic, llm_events):
        integration.patch()

        fake_anthropic.Messages().create(
            model="claude-3-5-sonnet", messages=[{"role": "user", "content": "hello"}]
        )

        assert len(_llm_start(llm_events())) == 1
        assert len(_llm_end(llm_events())) == 1

    def test_records_token_usage_and_cost(self, fake_anthropic, llm_events):
        integration.patch()
        client = fake_anthropic.Messages()
        client.response = StubResponse(StubUsage(input_tokens=1_000_000, output_tokens=0))

        client.create(model="claude-3-5-sonnet-20241022", messages=[])

        end = _llm_end(llm_events())[0].data
        assert end["input_tokens"] == 1_000_000
        assert end["cost_usd"] == pytest.approx(3.0)

    def test_system_prompt_is_captured(self, fake_anthropic, llm_events):
        integration.patch()

        fake_anthropic.Messages().create(
            model="claude-3-5-sonnet", messages=[], system="you are a helpful assistant"
        )

        assert _llm_start(llm_events())[0].data["system"] == "you are a helpful assistant"

    def test_system_prompt_absent_when_not_supplied(self, fake_anthropic, llm_events):
        integration.patch()
        fake_anthropic.Messages().create(model="claude-3-5-sonnet", messages=[])

        assert "system" not in _llm_start(llm_events())[0].data

    def test_thinking_blocks_are_captured(self, fake_anthropic, llm_events):
        integration.patch()
        client = fake_anthropic.Messages()
        client.response = StubResponse(
            StubUsage(input_tokens=1, output_tokens=1),
            content=[StubThinkingBlock("let me reason about this")],
        )

        client.create(model="claude-3-5-sonnet", messages=[])

        assert _llm_end(llm_events())[0].data["thinking_blocks"] == ["let me reason about this"]

    def test_thinking_blocks_key_omitted_when_response_has_none(self, fake_anthropic, llm_events):
        integration.patch()
        fake_anthropic.Messages().create(model="claude-3-5-sonnet", messages=[])

        assert "thinking_blocks" not in _llm_end(llm_events())[0].data

    def test_sdk_exception_propagates_and_is_recorded_as_error(self, fake_anthropic, reset_singletons):
        integration.patch()
        client = fake_anthropic.Messages()
        client.raises = RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError, match="upstream exploded"):
            client.create(model="claude-3-5-sonnet", messages=[])

        spans = [s for run in reset_singletons.get_runs(limit=10) for s in reset_singletons.get_spans(run.id)]
        assert any(s.status == "error" for s in spans)

    def test_api_key_is_not_persisted(self, fake_anthropic, llm_events):
        integration.patch()

        fake_anthropic.Messages().create(model="claude-3-5-sonnet", messages=[], api_key="sk-ant-secret")

        assert "sk-ant-secret" not in str([e.data for e in llm_events()])


class TestAsyncCapture:
    """
    Regression: the async branch was gated on ``hasattr(Messages, "acreate")``,
    which is False on every shipped SDK — so async Anthropic calls were captured
    by nothing, with no error to notice.
    """

    @pytest.mark.asyncio
    async def test_async_call_is_captured(self, fake_anthropic, llm_events):
        integration.patch()

        await fake_anthropic.AsyncMessages().create(
            model="claude-3-5-sonnet", messages=[{"role": "user", "content": "async hi"}]
        )

        assert len(_llm_start(llm_events())) == 1
        assert len(_llm_end(llm_events())) == 1

    @pytest.mark.asyncio
    async def test_async_captures_system_and_thinking_like_the_sync_path(
        self, fake_anthropic, llm_events
    ):
        """The old async branch dropped both; the shared payload builders fix that."""
        integration.patch()
        client = fake_anthropic.AsyncMessages()
        client.response = StubResponse(
            StubUsage(input_tokens=1, output_tokens=1),
            content=[StubThinkingBlock("async reasoning")],
        )

        await client.create(model="claude-3-5-sonnet", messages=[], system="be terse")

        assert _llm_start(llm_events())[0].data["system"] == "be terse"
        assert _llm_end(llm_events())[0].data["thinking_blocks"] == ["async reasoning"]

    @pytest.mark.asyncio
    async def test_async_span_is_named_separately_from_sync(self, fake_anthropic, reset_singletons):
        integration.patch()

        await fake_anthropic.AsyncMessages().create(model="claude-3-5-sonnet", messages=[])

        names = [s.name for run in reset_singletons.get_runs(limit=10) for s in reset_singletons.get_spans(run.id)]
        assert "anthropic.amessages(claude-3-5-sonnet)" in names


    @pytest.mark.asyncio
    async def test_async_exception_propagates_and_is_recorded_as_error(
        self, fake_anthropic, reset_singletons
    ):
        integration.patch()
        client = fake_anthropic.AsyncMessages()
        client.raises = ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            await client.create(model="claude-3-5-sonnet", messages=[])

        spans = [s for run in reset_singletons.get_runs(limit=10) for s in reset_singletons.get_spans(run.id)]
        assert any(s.status == "error" for s in spans)


class TestControlPlaneInjection:
    @pytest.mark.asyncio
    async def test_injected_result_short_circuits_the_async_sdk_call(
        self, fake_anthropic, llm_events
    ):
        from agent_lens.control import ControlPlane
        from agent_lens.tracer import Tracer

        integration.patch()
        run = Tracer.get_instance().start_run("async-injection-test")
        ControlPlane.get_instance().inject(run.id, {"injected": True})

        client = fake_anthropic.AsyncMessages()
        result = await client.create(model="claude-3-5-sonnet", messages=[])

        assert result == {"injected": True}
        assert client.calls == []
        assert _llm_end(llm_events()) == []

    def test_injected_result_short_circuits_the_sdk_call(self, fake_anthropic, llm_events):
        from agent_lens.control import ControlPlane
        from agent_lens.tracer import Tracer

        integration.patch()
        run = Tracer.get_instance().start_run("injection-test")
        ControlPlane.get_instance().inject(run.id, {"injected": True})

        client = fake_anthropic.Messages()
        result = client.create(model="claude-3-5-sonnet", messages=[])

        assert result == {"injected": True}
        assert client.calls == []
        assert _llm_end(llm_events()) == []
