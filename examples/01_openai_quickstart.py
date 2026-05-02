"""
Example 01: OpenAI quickstart with agent-lens

This is the zero-configuration path:
1. Install agent-lens: pip install agent-lens
2. Call agent_lens.install() once at startup
3. All OpenAI calls are traced automatically

Run this example:
    export OPENAI_API_KEY=your-key
    python examples/01_openai_quickstart.py
"""

import agent_lens
from openai import OpenAI

# Auto-patch OpenAI SDK: all calls are now traced automatically
agent_lens.install()

# Open the dashboard in your browser
agent_lens.dashboard.start()

client = OpenAI()


@agent_lens.trace
def research_agent(query: str) -> str:
    """A simple research agent that uses GPT-4o-mini."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Research: {query}"}],
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    print("Running research_agent...")
    result = research_agent("What are the main features of Python 3.12?")
    print(f"\nAgent result:\n{result}")
    print("\nView the trace at http://127.0.0.1:7878")
    print("Press Ctrl+C to stop.")

    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDone.")
