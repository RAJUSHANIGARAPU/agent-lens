"""
Example 02: Anthropic tool-calling agent with agent-lens

Demonstrates:
- Auto-patching Anthropic SDK with agent_lens.install()
- Tool-calling flow captured as nested spans
- Cost estimation (input/output tokens tracked)

Set ANTHROPIC_API_KEY=your-key to use the real API.
Without a key, the example runs in demo mode with mocked responses.

Run:
    export ANTHROPIC_API_KEY=your-key  # optional
    python examples/02_anthropic_tools.py
"""

import json
import os
import time
from typing import Any
from unittest.mock import MagicMock

import agent_lens

# Auto-patch Anthropic SDK
agent_lens.install(openai=False, anthropic=True)

# Start dashboard
agent_lens.dashboard.start()

# ----------------------------------------------------------------
# Tool definitions (for Claude)
# ----------------------------------------------------------------

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a given location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City and country, e.g. Amsterdam, Netherlands",
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit",
                },
            },
            "required": ["location"],
        },
    }
]


def get_weather(location: str, unit: str = "celsius") -> dict:
    """Simulated weather tool — returns fake data."""
    # In production, this would call a real weather API
    return {
        "location": location,
        "temperature": 18,
        "unit": unit,
        "condition": "partly cloudy",
        "humidity": 65,
    }


# ----------------------------------------------------------------
# Demo mode: mock Anthropic if no key is set
# ----------------------------------------------------------------

def build_mock_client():
    """Build a mock Anthropic client for keyless demo mode."""
    mock_response = MagicMock()
    mock_response.stop_reason = "tool_use"
    mock_response.usage = MagicMock(input_tokens=25, output_tokens=40)
    mock_response.content = [
        MagicMock(
            type="tool_use",
            id="toolu_01",
            name="get_weather",
            input={"location": "Amsterdam, Netherlands", "unit": "celsius"},
        )
    ]

    final_response = MagicMock()
    final_response.stop_reason = "end_turn"
    final_response.usage = MagicMock(input_tokens=80, output_tokens=45)
    final_response.content = [
        MagicMock(
            type="text",
            text="The weather in Amsterdam is currently 18°C and partly cloudy with 65% humidity.",
        )
    ]

    call_count = [0]
    def create_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_response
        return final_response

    mock = MagicMock()
    mock.messages.create.side_effect = create_side_effect
    return mock


# ----------------------------------------------------------------
# The agent
# ----------------------------------------------------------------

@agent_lens.trace
def weather_agent(user_question: str) -> str:
    """
    A tool-calling agent that answers weather questions.
    Demonstrates agent-lens capturing multi-turn tool use.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    else:
        print("[demo mode] No ANTHROPIC_API_KEY set — using mocked responses")
        client = build_mock_client()

    messages = [{"role": "user", "content": user_question}]

    # First turn: Claude decides to use a tool
    response = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=512,
        tools=TOOLS,
        messages=messages,
    )

    # Handle tool use
    if response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input if isinstance(block.input, dict) else {}

                # Execute the tool
                if tool_name == "get_weather":
                    @agent_lens.trace(span_type="tool")
                    def call_weather_tool():
                        return get_weather(**tool_input)

                    result = call_weather_tool()
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        # Second turn: Claude uses tool results to answer
        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

        final_response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=512,
            tools=TOOLS,
            messages=messages,
        )

        for block in final_response.content:
            if hasattr(block, "text"):
                return block.text

    # Direct response (no tool use)
    for block in response.content:
        if hasattr(block, "text"):
            return block.text

    return "No response generated."


if __name__ == "__main__":
    print("Running weather agent...")
    answer = weather_agent("What's the weather like in Amsterdam right now?")
    print(f"\nAgent answer:\n{answer}")
    print("\nView the trace at http://127.0.0.1:7878")
    print("You'll see: root span → LLM call span → tool span → LLM call span")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDone.")
