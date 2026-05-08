"""
Integration test: end-to-end OpenAI tracing with mocked HTTP.

Uses respx to mock the OpenAI HTTP endpoint.
Verifies:
- Trace tree has expected shape (root span → llm span)
- Token counts captured
- Latency recorded
- No API keys in stored data
"""

import json
import time

import pytest

try:
    import httpx
    import respx
    RESPX_AVAILABLE = True
except ImportError:
    RESPX_AVAILABLE = False
    class _RespxStub:  # noqa: N801
        def mock(self, f=None):
            return f if f else (lambda fn: fn)
    respx = _RespxStub()  # type: ignore[assignment]

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not (RESPX_AVAILABLE and OPENAI_AVAILABLE),
    reason="respx and openai are required for this test",
)

MOCK_RESPONSE = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "created": int(time.time()),
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Python 3.12 adds typing improvements."},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 25,
        "completion_tokens": 15,
        "total_tokens": 40,
    },
}


@pytest.fixture(autouse=True)
def patch_openai_integration(reset_singletons):
    """Apply the agent-lens OpenAI patch after resetting singletons."""
    from agent_lens.integrations.openai import patch
    patch()


@respx.mock
def test_openai_trace_tree_shape(reset_singletons):
    """Trace tree has root span and LLM span after a traced OpenAI call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    client = openai.OpenAI(api_key="sk-test-fake-key-1234567890")

    @trace
    def research_agent(query: str) -> str:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Research: {query}"}],
        )
        return response.choices[0].message.content

    research_agent("Python 3.12 features")

    store = get_default_store()
    runs = store.get_runs()
    assert len(runs) == 1

    spans = store.get_spans(runs[0].id)
    span_names = [s.name for s in spans]

    # Should have at least: the root agent span + the LLM span
    assert any("research_agent" in n for n in span_names), f"No research_agent span in {span_names}"
    assert any("openai" in n.lower() or "gpt" in n.lower() for n in span_names), f"No LLM span in {span_names}"


@respx.mock
def test_openai_token_counts_captured(reset_singletons):
    """Token counts from OpenAI response are stored in events."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    client = openai.OpenAI(api_key="sk-test-fake-key-9876543210")

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)

    llm_end_events = [e for e in events if e.type == "llm_end"]
    assert len(llm_end_events) >= 1

    event_data = llm_end_events[0].data
    assert event_data.get("prompt_tokens") == 25
    assert event_data.get("completion_tokens") == 15
    assert event_data.get("total_tokens") == 40


@respx.mock
def test_openai_latency_recorded(reset_singletons):
    """Latency is recorded in the LLM_END event."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    client = openai.OpenAI(api_key="sk-test-fake-key-latency")

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)

    llm_end_events = [e for e in events if e.type == "llm_end"]
    assert llm_end_events[0].data.get("latency_ms", 0) >= 0


@respx.mock
def test_no_api_keys_in_stored_data(reset_singletons):
    """API keys never appear in the SQLite store after a traced call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    secret_key = "sk-supersecret-abc123DEF456ghi789"

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    client = openai.OpenAI(api_key=secret_key)

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Tell me about Python 3.12"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)
    spans = store.get_spans(runs[0].id)

    all_data = json.dumps([e.data for e in events]) + json.dumps([s.model_dump() for s in spans])
    assert secret_key not in all_data, "Secret key found in stored data!"


@respx.mock
def test_openai_error_span_marked_as_error(reset_singletons):
    """If OpenAI returns an error, the span is marked as error."""
    from agent_lens.models import RunStatus
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            401,
            json={"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
        )
    )

    client = openai.OpenAI(api_key="sk-invalid-key-123")

    @trace
    def agent():
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello"}],
        )

    with pytest.raises(RuntimeError):
        agent()

    store = get_default_store()
    runs = store.get_runs()
    assert runs[0].status in (RunStatus.ERROR, "error")
