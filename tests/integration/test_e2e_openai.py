"""
End-to-end OpenAI tracing against a mocked transport.

The mock is installed on the client under test via `http_client=` (see conftest),
not patched into httpx globally, so the SDK's real request building and error
handling run while nothing leaves the machine.

Verifies:
- Trace tree has expected shape (root span → llm span)
- Token counts captured
- Latency recorded
- No API keys in stored data, or in what would have been sent
- An API error marks the span and the run as failed
"""

import json
import time

import pytest

pytest.importorskip("openai", reason="openai SDK not installed")

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
    from agent_lens.integrations.openai import patch, unpatch

    patch()
    yield
    unpatch()


def test_openai_trace_tree_shape(reset_singletons, mock_openai):
    """Trace tree has root span and LLM span after a traced OpenAI call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_openai(MOCK_RESPONSE)

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

    span_names = [s.name for s in store.get_spans(runs[0].id)]

    assert any("research_agent" in n for n in span_names), f"No research_agent span in {span_names}"
    assert any(
        "openai" in n.lower() or "gpt" in n.lower() for n in span_names
    ), f"No LLM span in {span_names}"


def test_the_request_never_left_the_machine(reset_singletons, mock_openai):
    """
    Positive control for the mock itself.

    The previous respx-based mock silently stopped intercepting and these tests
    began calling api.openai.com for real. A test that cannot tell the difference
    between a mocked call and a live one is not a mocked test.
    """
    from agent_lens.tracer import trace

    client, transport = mock_openai(MOCK_RESPONSE)

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}]
        )

    result = agent()

    assert len(transport.requests) == 1, "the mocked transport was not the one used"
    assert str(transport.requests[0].url).endswith("/chat/completions")
    assert result.choices[0].message.content == MOCK_RESPONSE["choices"][0]["message"]["content"]


def test_openai_token_counts_captured(reset_singletons, mock_openai):
    """Token counts from the OpenAI response are stored in events."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_openai(MOCK_RESPONSE)

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}]
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)

    llm_end_events = [e for e in events if e.type == "llm_end" and "prompt_tokens" in e.data]
    assert len(llm_end_events) >= 1

    data = llm_end_events[0].data
    assert data["prompt_tokens"] == 25
    assert data["completion_tokens"] == 15
    assert data["total_tokens"] == 40


def test_openai_latency_recorded(reset_singletons, mock_openai):
    """Latency is recorded in the LLM_END event."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_openai(MOCK_RESPONSE)

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}]
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)
    llm_end = [e for e in events if e.type == "llm_end" and "latency_ms" in e.data]
    assert llm_end[0].data["latency_ms"] >= 0


def test_no_api_keys_in_stored_data(reset_singletons, mock_openai):
    """API keys never appear in the SQLite store after a traced call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    secret_key = "sk-supersecret-abc123DEF456ghi789"
    client, transport = mock_openai(MOCK_RESPONSE, api_key=secret_key)

    @trace
    def agent():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Tell me about Python 3.12"}],
        )

    agent()

    store = get_default_store()
    run_id = store.get_runs()[0].id
    events = store.get_events(run_id)
    spans = store.get_spans(run_id)

    stored = json.dumps([e.data for e in events]) + json.dumps([s.model_dump() for s in spans])
    assert secret_key not in stored, "Secret key found in stored data!"

    # The key really was in play — it went out on the wire as an auth header — so
    # its absence from the store is redaction working, not the key never existing.
    assert secret_key in str(dict(transport.requests[0].headers))


def test_openai_error_span_marked_as_error(reset_singletons, mock_openai):
    """If OpenAI returns an error, the span and run are marked as errored."""
    import openai

    from agent_lens.models import RunStatus
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_openai(
        {"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
        status=401,
    )

    @trace
    def agent():
        client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}]
        )

    # The SDK's own error type, not a generic RuntimeError — the previous
    # expectation was never validated because this suite always skipped.
    with pytest.raises(openai.AuthenticationError):
        agent()

    store = get_default_store()
    runs = store.get_runs()
    assert runs[0].status in (RunStatus.ERROR, "error")
