"""
OpenAI capture layer.

Exercised against a stub SDK installed into sys.modules (see conftest), so these
run offline with no vendor dependency. The stub's shape is pinned against the
real SDK by test_sdk_surface.py.
"""

from __future__ import annotations

import pytest

from agent_lens.integrations import openai as integration

from .conftest import StubResponse, StubUsage


def _llm_start(events):
    return [e for e in events if e.data.get("provider") == "openai" and "messages" in e.data]


def _llm_end(events):
    return [e for e in events if e.data.get("provider") == "openai" and "latency_ms" in e.data]


class TestPatchLifecycle:
    def test_patch_returns_false_when_sdk_absent(self, sdk_absent):
        sdk_absent("openai")

        assert integration.patch() is False

    def test_patch_wraps_both_sync_and_async_call_sites(self, fake_openai):
        original_sync = fake_openai.Completions.create
        original_async = fake_openai.AsyncCompletions.create

        assert integration.patch() is True

        assert fake_openai.Completions.create is not original_sync
        assert fake_openai.AsyncCompletions.create is not original_async

    def test_patch_is_idempotent(self, fake_openai):
        assert integration.patch() is True
        wrapper = fake_openai.Completions.create

        assert integration.patch() is True
        assert fake_openai.Completions.create is wrapper

    def test_missing_async_class_is_skipped_not_fatal(self, fake_openai):
        """
        Regression: the integration previously read ``Completions.acreate``, an
        attribute no OpenAI SDK >=1.0 has ever defined. That raised
        AttributeError out of ``agent_lens.install()`` — the package's advertised
        one-line entry point — for every user with the SDK installed.

        A call site the SDK does not expose must reduce capture, never raise.
        """
        del fake_openai.AsyncCompletions

        assert integration.patch() is True
        assert fake_openai.Completions.create.__name__ == "_patched_create"

    def test_patch_returns_false_when_no_call_site_exists(self, fake_openai):
        del fake_openai.Completions
        del fake_openai.AsyncCompletions

        assert integration.patch() is False

    def test_unpatch_restores_the_original_callable(self, fake_openai):
        original = fake_openai.Completions.create

        integration.patch()
        assert fake_openai.Completions.create is not original

        integration.unpatch()
        assert fake_openai.Completions.create is original

    def test_patch_unpatch_cycle_does_not_nest_wrappers(self, fake_openai, llm_events):
        """
        Regression: unpatch() used to only clear the flag without restoring the
        original, so the next patch() wrapped the wrapper and every call emitted
        duplicate spans and events.
        """
        for _ in range(3):
            integration.patch()
            integration.unpatch()
        integration.patch()

        fake_openai.Completions().create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

        assert len(_llm_end(llm_events())) == 1

    def test_call_site_without_the_method_is_skipped(self, fake_openai):
        """A class present but missing ``create`` must be stepped over, not patched."""
        del fake_openai.Completions.create

        assert integration.patch() is True
        assert not hasattr(fake_openai.Completions, "create")

    def test_unpatch_is_safe_when_not_patched(self, fake_openai):
        integration.unpatch()  # must not raise


class TestSyncCapture:
    def test_records_start_and_end_events(self, fake_openai, llm_events):
        integration.patch()
        client = fake_openai.Completions()

        client.create(model="gpt-4o", messages=[{"role": "user", "content": "hello"}])

        starts = _llm_start(llm_events())
        ends = _llm_end(llm_events())
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].data["model"] == "gpt-4o"
        assert starts[0].data["messages"] == [{"role": "user", "content": "hello"}]

    def test_records_token_usage_and_cost(self, fake_openai, llm_events):
        integration.patch()
        client = fake_openai.Completions()
        client.response = StubResponse(StubUsage(prompt_tokens=1_000, completion_tokens=1_000))

        client.create(model="gpt-4o-mini", messages=[])

        end = _llm_end(llm_events())[0].data
        assert end["prompt_tokens"] == 1_000
        assert end["completion_tokens"] == 1_000
        assert end["total_tokens"] == 2_000
        assert end["cost_usd"] == pytest.approx(0.00075)
        assert end["latency_ms"] >= 0

    def test_response_without_usage_reports_zero_tokens(self, fake_openai, llm_events):
        integration.patch()
        client = fake_openai.Completions()
        client.response = StubResponse(usage=None)

        client.create(model="gpt-4o", messages=[])

        end = _llm_end(llm_events())[0].data
        assert end["prompt_tokens"] == 0
        assert end["completion_tokens"] == 0
        assert end["cost_usd"] == 0.0

    def test_call_is_delegated_unchanged_and_result_returned(self, fake_openai):
        integration.patch()
        client = fake_openai.Completions()
        staged = StubResponse(StubUsage(prompt_tokens=1, completion_tokens=1))
        client.response = staged

        result = client.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}], temperature=0.2)

        assert result is staged
        assert client.calls == [
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2}
        ]

    def test_sdk_exception_propagates_and_is_recorded_as_error(self, fake_openai, reset_singletons):
        integration.patch()
        client = fake_openai.Completions()
        client.raises = RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError, match="upstream exploded"):
            client.create(model="gpt-4o", messages=[])

        spans = [s for run in reset_singletons.get_runs(limit=10) for s in reset_singletons.get_spans(run.id)]
        assert any(s.status == "error" for s in spans)

    def test_api_key_is_not_persisted(self, fake_openai, llm_events):
        integration.patch()
        client = fake_openai.Completions()

        client.create(model="gpt-4o", messages=[], api_key="sk-super-secret")

        assert "sk-super-secret" not in str([e.data for e in llm_events()])

    def test_model_falls_back_when_not_supplied(self, fake_openai, llm_events):
        integration.patch()
        fake_openai.Completions().create(messages=[])

        assert _llm_start(llm_events())[0].data["model"] == "unknown"


class TestAsyncCapture:
    """
    Regression: the async path was bound to a non-existent attribute, so async
    OpenAI calls were captured by nothing at all — silently, with no error.
    """

    @pytest.mark.asyncio
    async def test_async_call_is_captured(self, fake_openai, llm_events):
        integration.patch()
        client = fake_openai.AsyncCompletions()

        await client.create(model="gpt-4o", messages=[{"role": "user", "content": "async hi"}])

        assert len(_llm_start(llm_events())) == 1
        assert len(_llm_end(llm_events())) == 1

    @pytest.mark.asyncio
    async def test_async_span_is_named_separately_from_sync(self, fake_openai, reset_singletons):
        integration.patch()

        await fake_openai.AsyncCompletions().create(model="gpt-4o", messages=[])

        names = [s.name for run in reset_singletons.get_runs(limit=10) for s in reset_singletons.get_spans(run.id)]
        assert "openai.achat(gpt-4o)" in names

    @pytest.mark.asyncio
    async def test_async_exception_propagates(self, fake_openai):
        integration.patch()
        client = fake_openai.AsyncCompletions()
        client.raises = ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            await client.create(model="gpt-4o", messages=[])


class TestControlPlaneInjection:
    @pytest.mark.asyncio
    async def test_injected_result_short_circuits_the_async_sdk_call(self, fake_openai, llm_events):
        from agent_lens.control import ControlPlane
        from agent_lens.tracer import Tracer

        integration.patch()
        run = Tracer.get_instance().start_run("async-injection-test")
        ControlPlane.get_instance().inject(run.id, {"injected": True})

        client = fake_openai.AsyncCompletions()
        result = await client.create(model="gpt-4o", messages=[])

        assert result == {"injected": True}
        assert client.calls == []
        assert _llm_end(llm_events()) == []

    def test_injected_result_short_circuits_the_sdk_call(self, fake_openai, llm_events):
        """A forked/injected result must be returned without calling the vendor."""
        from agent_lens.control import ControlPlane
        from agent_lens.tracer import Tracer

        integration.patch()
        tracer = Tracer.get_instance()
        run = tracer.start_run("injection-test")

        control = ControlPlane.get_instance()
        control.inject(run.id, {"injected": True})

        client = fake_openai.Completions()
        result = client.create(model="gpt-4o", messages=[])

        assert result == {"injected": True}
        assert client.calls == []
        assert _llm_end(llm_events()) == []
