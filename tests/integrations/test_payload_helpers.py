"""
Payload builders shared by the provider integrations.

These run on whatever the vendor hands back, which is not always a well-behaved
pydantic model — a tracer that raises while describing a response breaks the
application it is meant to observe, so every branch here must degrade instead.
"""

from __future__ import annotations

import pytest

from agent_lens.integrations import anthropic as anthropic_integration
from agent_lens.integrations import openai as openai_integration


class Dumpable:
    """Mirrors a pydantic message/response object."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self) -> dict:
        return dict(self._payload)


class Exploding:
    """A vendor object whose serialisation raises."""

    def model_dump(self):
        raise RuntimeError("cannot serialise")


class PlainAttrs:
    """A vendor object with no model_dump, only attributes."""

    def __init__(self) -> None:
        self.role = "assistant"
        self._private = "should not be captured"


@pytest.mark.parametrize(
    "integration", [openai_integration, anthropic_integration], ids=["openai", "anthropic"]
)
class TestExtractMessages:
    def test_non_list_input_yields_empty(self, integration):
        assert integration._extract_messages("not a list") == []
        assert integration._extract_messages(None) == []

    def test_dict_messages_pass_through(self, integration):
        messages = [{"role": "user", "content": "hi"}]

        assert integration._extract_messages(messages) == messages

    def test_pydantic_style_message_is_dumped(self, integration):
        result = integration._extract_messages([Dumpable({"role": "user", "content": "hi"})])

        assert result == [{"role": "user", "content": "hi"}]

    def test_unserialisable_message_degrades_to_a_string(self, integration):
        result = integration._extract_messages([Exploding()])

        assert result[0]["role"] == "unknown"
        assert "Exploding" in result[0]["content"]

    def test_api_key_inside_a_message_is_redacted(self, integration):
        # Redaction matches on the value's shape, not the key name, so the
        # fixture has to be a realistically-shaped key.
        key = "sk-abcdefghijklmnop1234567890"
        result = integration._extract_messages([{"role": "user", "content": key}])

        assert key not in str(result)
        assert "[REDACTED]" in str(result)


@pytest.mark.parametrize(
    "integration", [openai_integration, anthropic_integration], ids=["openai", "anthropic"]
)
class TestSafeResponseDict:
    def test_pydantic_style_response_is_dumped(self, integration):
        assert integration._safe_response_dict(Dumpable({"id": "resp_1"})) == {"id": "resp_1"}

    def test_plain_object_falls_back_to_public_attributes(self, integration):
        result = integration._safe_response_dict(PlainAttrs())

        assert result == {"role": "assistant"}

    def test_unserialisable_response_degrades_to_empty(self, integration):
        assert integration._safe_response_dict(Exploding()) == {}

    def test_object_with_neither_shape_degrades_to_empty(self, integration):
        assert integration._safe_response_dict(object()) == {}


class TestExtractThinkingBlocks:
    def test_absent_content_yields_no_blocks(self):
        assert anthropic_integration._extract_thinking_blocks(object()) == []

    def test_non_thinking_blocks_are_ignored(self):
        class TextBlock:
            type = "text"

        response = type("R", (), {"content": [TextBlock()]})()

        assert anthropic_integration._extract_thinking_blocks(response) == []

    def test_hostile_content_degrades_instead_of_raising(self):
        class Hostile:
            @property
            def content(self):
                raise RuntimeError("no content for you")

        assert anthropic_integration._extract_thinking_blocks(Hostile()) == []
