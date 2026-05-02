"""
Example 03: LangChain chain with AgentLensCallbackHandler

Demonstrates:
- Using AgentLensCallbackHandler to trace a LangChain pipeline
- Works with LangChain's callbacks interface
- All chain events, LLM calls, and tool calls are captured

Install LangChain:
    pip install langchain langchain-openai

Run:
    export OPENAI_API_KEY=your-key
    python examples/03_langchain_chain.py
"""

import os
import time
from unittest.mock import MagicMock, patch

import agent_lens

# Start the dashboard
agent_lens.dashboard.start()

# ----------------------------------------------------------------
# Demo mode helpers
# ----------------------------------------------------------------

def build_mock_langchain():
    """Create mock LangChain components for demo without API key."""
    from unittest.mock import MagicMock

    # Mock LLMResult
    generation = MagicMock()
    generation.text = "Python 3.12 introduces several improvements including better error messages, f-string enhancements, type parameter syntax, and performance improvements."

    llm_result = MagicMock()
    llm_result.generations = [[generation]]
    llm_result.llm_output = {
        "token_usage": {
            "prompt_tokens": 30,
            "completion_tokens": 45,
            "total_tokens": 75,
        }
    }
    return llm_result


# ----------------------------------------------------------------
# LangChain example
# ----------------------------------------------------------------

def run_langchain_example():
    try:
        from langchain_core.prompts import PromptTemplate
        LANGCHAIN_AVAILABLE = True
    except ImportError:
        try:
            from langchain.prompts import PromptTemplate
            LANGCHAIN_AVAILABLE = True
        except ImportError:
            LANGCHAIN_AVAILABLE = False

    if not LANGCHAIN_AVAILABLE:
        print("LangChain not installed. Install with: pip install langchain langchain-openai")
        print("Running in stub mode to demonstrate the callback handler interface...")
        _run_stub_demo()
        return

    from agent_lens.integrations.langchain import AgentLensCallbackHandler

    handler = AgentLensCallbackHandler()

    api_key = os.getenv("OPENAI_API_KEY")

    if api_key:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                callbacks=[handler],
                openai_api_key=api_key,
            )
        except ImportError:
            from langchain.chat_models import ChatOpenAI
            llm = ChatOpenAI(
                model_name="gpt-4o-mini",
                callbacks=[handler],
                openai_api_key=api_key,
            )
    else:
        print("[demo mode] No OPENAI_API_KEY set — using mock LLM")
        _run_stub_demo()
        return

    try:
        from langchain_core.prompts import PromptTemplate
        from langchain_core.output_parsers import StrOutputParser
    except ImportError:
        from langchain.prompts import PromptTemplate
        from langchain.schema import StrOutputParser

    prompt = PromptTemplate.from_template(
        "You are a Python expert. Explain the key features of {topic} in 2-3 sentences."
    )

    chain = prompt | llm | StrOutputParser()

    print("Running LangChain chain with agent-lens callback handler...")
    result = chain.invoke(
        {"topic": "Python 3.12"},
        config={"callbacks": [handler]},
    )
    print(f"\nChain output:\n{result}")


def _run_stub_demo():
    """Demonstrate the callback handler interface without a real LLM."""
    from agent_lens.integrations.langchain import AgentLensCallbackHandler
    import uuid

    handler = AgentLensCallbackHandler()

    run_id = uuid.uuid4()

    # Simulate what LangChain would call on our handler
    print("\nSimulating LangChain callback sequence:")

    print("  → on_chain_start")
    handler.on_chain_start(
        serialized={"name": "RetrievalChain", "id": ["langchain", "chains", "retrieval"]},
        inputs={"input": "What are Python 3.12 features?"},
        run_id=run_id,
    )

    print("  → on_llm_start")
    llm_run_id = uuid.uuid4()
    handler.on_llm_start(
        serialized={"name": "ChatOpenAI", "id": ["langchain", "chat_models", "openai"]},
        prompts=["You are a Python expert. Explain the key features of Python 3.12."],
        run_id=llm_run_id,
        parent_run_id=run_id,
    )

    print("  → on_llm_end")
    # Build a mock response object
    mock_result = build_mock_langchain()
    handler.on_llm_end(mock_result, run_id=llm_run_id, parent_run_id=run_id)

    print("  → on_tool_start")
    tool_run_id = uuid.uuid4()
    handler.on_tool_start(
        serialized={"name": "PythonREPLTool"},
        input_str="print('Python 3.12 features')",
        run_id=tool_run_id,
        parent_run_id=run_id,
    )

    print("  → on_tool_end")
    handler.on_tool_end(
        output="Python 3.12 features: ...",
        run_id=tool_run_id,
        parent_run_id=run_id,
    )

    print("  → on_chain_end")
    handler.on_chain_end(
        outputs={"output": "Python 3.12 has improved error messages and f-strings."},
        run_id=run_id,
    )

    print("\nCallback sequence complete.")
    print("Check the dashboard to see the captured spans.")


if __name__ == "__main__":
    run_langchain_example()

    print("\nView the trace at http://127.0.0.1:7878")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDone.")
