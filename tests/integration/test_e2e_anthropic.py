"""
End-to-end Anthropic tracing against a mocked transport.

The mock is installed on the client under test via `http_client=` (see conftest),
not patched into httpx globally, so the SDK's real request building and error
handling run while nothing leaves the machine.

Verifies:
- Trace tree has expected shape
- Token counts captured
- Latency recorded
- Cost estimated
- Extended thinking blocks captured
- No API keys in stored data, or in what would have been sent
"""

import json

import pytest

pytest.importorskip("anthropic", reason="anthropic SDK not installed")

MOCK_ANTHROPIC_RESPONSE = {
    "id": "msg-test123",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Claude says hello from the mock!"}],
    "model": "claude-3-haiku-20240307",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 18,
        "output_tokens": 12,
    },
}


@pytest.fixture(autouse=True)
def patch_anthropic_integration(reset_singletons):
    """Apply the agent-lens Anthropic patch."""
    from agent_lens.integrations.anthropic import patch, unpatch

    patch()
    yield
    unpatch()


def test_anthropic_trace_tree_shape(reset_singletons, mock_anthropic):
    """Trace tree has root span and LLM span after an Anthropic call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_anthropic(MOCK_ANTHROPIC_RESPONSE)

    @trace
    def my_agent() -> str:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello, Claude!"}],
        )
        return response.content[0].text

    my_agent()

    store = get_default_store()
    runs = store.get_runs()
    assert len(runs) == 1

    span_names = [s.name for s in store.get_spans(runs[0].id)]

    assert any("my_agent" in n for n in span_names), f"No my_agent span in {span_names}"
    assert any(
        "anthropic" in n.lower() or "claude" in n.lower() for n in span_names
    ), f"No LLM span in {span_names}"


def test_the_request_never_left_the_machine(reset_singletons, mock_anthropic):
    """
    Positive control for the mock itself — see the OpenAI twin.

    Without this, a mock that quietly stops intercepting looks identical to a
    mock that works, which is exactly how this suite came to call the live API.
    """
    from agent_lens.tracer import trace

    client, transport = mock_anthropic(MOCK_ANTHROPIC_RESPONSE)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    result = agent()

    assert len(transport.requests) == 1, "the mocked transport was not the one used"
    assert str(transport.requests[0].url).endswith("/messages")
    assert result.content[0].text == MOCK_ANTHROPIC_RESPONSE["content"][0]["text"]


def test_anthropic_token_counts_captured(reset_singletons, mock_anthropic):
    """Input/output token counts from the Anthropic response are stored in events."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_anthropic(MOCK_ANTHROPIC_RESPONSE)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Test"}],
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)

    llm_end_events = [e for e in events if e.type == "llm_end" and "input_tokens" in e.data]
    assert len(llm_end_events) >= 1

    data = llm_end_events[0].data
    assert data["input_tokens"] == 18
    assert data["output_tokens"] == 12


def test_anthropic_latency_recorded(reset_singletons, mock_anthropic):
    """Latency is captured in the LLM_END event."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_anthropic(MOCK_ANTHROPIC_RESPONSE)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)
    llm_end = [e for e in events if e.type == "llm_end" and "latency_ms" in e.data]
    assert llm_end[0].data["latency_ms"] >= 0


def test_anthropic_cost_estimate(reset_singletons, mock_anthropic):
    """Cost estimate is recorded for Anthropic calls."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    client, _ = mock_anthropic(MOCK_ANTHROPIC_RESPONSE)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "What's the weather?"}],
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)
    llm_end = [e for e in events if e.type == "llm_end" and "cost_usd" in e.data]
    assert llm_end[0].data["cost_usd"] >= 0


def test_thinking_blocks_captured(reset_singletons, mock_anthropic):
    """Extended thinking blocks are stored in the llm_end event when present."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    thinking_response = {
        **MOCK_ANTHROPIC_RESPONSE,
        "content": [
            {"type": "thinking", "thinking": "Let me reason step by step...", "signature": "sig"},
            {"type": "text", "text": "The answer is 42."},
        ],
    }

    client, _ = mock_anthropic(thinking_response)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=200,
            messages=[{"role": "user", "content": "Solve this step by step"}],
        )

    agent()

    store = get_default_store()
    events = store.get_events(store.get_runs()[0].id)
    llm_end = [e for e in events if e.type == "llm_end" and "thinking_blocks" in e.data]
    assert len(llm_end) >= 1

    thinking = llm_end[0].data["thinking_blocks"]
    assert len(thinking) == 1
    assert "Let me reason step by step" in thinking[0]


def test_anthropic_no_api_keys_in_store(reset_singletons, mock_anthropic):
    """Anthropic API keys are never stored in SQLite."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    secret = "sk-ant-ultrasecret-abc123DEF456"
    client, transport = mock_anthropic(MOCK_ANTHROPIC_RESPONSE, api_key=secret)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    agent()

    store = get_default_store()
    run_id = store.get_runs()[0].id
    events = store.get_events(run_id)
    spans = store.get_spans(run_id)

    stored = json.dumps([e.data for e in events]) + json.dumps([s.model_dump() for s in spans])
    assert secret not in stored, "Anthropic API key found in stored data!"

    # The key was genuinely in play — it went out as an auth header — so its
    # absence from the store is redaction, not the key never existing.
    assert secret in str(dict(transport.requests[0].headers))
