"""
Integration test: end-to-end Anthropic tracing with mocked HTTP.

Uses respx to mock the Anthropic HTTP endpoint.
Verifies:
- Trace tree has expected shape
- Token counts captured
- Latency recorded
- No API keys in stored data
"""

import json
import time
import uuid

import pytest

try:
    import respx
    import httpx
    RESPX_AVAILABLE = True
except ImportError:
    RESPX_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not (RESPX_AVAILABLE and ANTHROPIC_AVAILABLE),
    reason="respx and anthropic are required for this test",
)

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
    from agent_lens.integrations.anthropic import patch
    patch()


@respx.mock
def test_anthropic_trace_tree_shape(reset_singletons):
    """Trace tree has root span and LLM span after an Anthropic call."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=MOCK_ANTHROPIC_RESPONSE)
    )

    client = anthropic.Anthropic(api_key="sk-ant-test-fake-key-1234")

    @trace
    def my_agent() -> str:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello, Claude!"}],
        )
        return response.content[0].text

    result = my_agent()

    store = get_default_store()
    runs = store.get_runs()
    assert len(runs) == 1

    spans = store.get_spans(runs[0].id)
    span_names = [s.name for s in spans]

    assert any("my_agent" in n for n in span_names), f"No my_agent span in {span_names}"
    assert any("anthropic" in n.lower() or "claude" in n.lower() for n in span_names), f"No LLM span in {span_names}"


@respx.mock
def test_anthropic_token_counts_captured(reset_singletons):
    """Input/output token counts from Anthropic response are stored in events."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=MOCK_ANTHROPIC_RESPONSE)
    )

    client = anthropic.Anthropic(api_key="sk-ant-test-key-5678")

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Test"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)

    llm_end_events = [e for e in events if e.type == "llm_end"]
    assert len(llm_end_events) >= 1

    event_data = llm_end_events[0].data
    assert event_data.get("input_tokens") == 18
    assert event_data.get("output_tokens") == 12


@respx.mock
def test_anthropic_latency_recorded(reset_singletons):
    """Latency is captured in the LLM_END event."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=MOCK_ANTHROPIC_RESPONSE)
    )

    client = anthropic.Anthropic(api_key="sk-ant-test-latency")

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)
    llm_end = [e for e in events if e.type == "llm_end"]
    assert llm_end[0].data.get("latency_ms", -1) >= 0


@respx.mock
def test_anthropic_no_api_keys_in_store(reset_singletons):
    """Anthropic API keys are never stored in SQLite."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    secret = "sk-ant-ultrasecret-abc123DEF456"

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=MOCK_ANTHROPIC_RESPONSE)
    )

    client = anthropic.Anthropic(api_key=secret)

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)
    spans = store.get_spans(runs[0].id)

    all_data = json.dumps([e.data for e in events]) + json.dumps([s.model_dump() for s in spans])
    assert secret not in all_data, f"Anthropic API key found in stored data!"


@respx.mock
def test_anthropic_cost_estimate(reset_singletons):
    """Cost estimate is recorded for Anthropic calls."""
    from agent_lens.store import get_default_store
    from agent_lens.tracer import trace

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=MOCK_ANTHROPIC_RESPONSE)
    )

    client = anthropic.Anthropic(api_key="sk-ant-test-cost")

    @trace
    def agent():
        return client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
            messages=[{"role": "user", "content": "What's the weather?"}],
        )

    agent()

    store = get_default_store()
    runs = store.get_runs()
    events = store.get_events(runs[0].id)
    llm_end = [e for e in events if e.type == "llm_end"]
    # Cost should be a non-negative float
    assert llm_end[0].data.get("cost_usd", -1) >= 0
