"""
Unit tests for the provider integration capture path
(agent_lens/integrations/{openai,anthropic,langchain,llamaindex}.py).

These modules are the load-bearing instrumentation: they monkey-patch (or hook
into) each provider SDK and translate real calls into agent-lens spans/events.
Previously they had 0% unit coverage — the only tests that touched them were
end-to-end tests that `skip` without a real API key and the provider package
installed, so a regression in the capture/redaction logic could ship silently.

To exercise the real wrapper logic in CI (where the heavy provider SDKs are NOT
installed — only `.[dev]`), we synthesize minimal fake provider modules/symbols
in ``sys.modules`` and drive the patched functions / callback handlers directly.
No network, no API keys, no provider dependency. respx would instead require
adding the real provider SDKs to the CI dependency set; faking the seams keeps
CI light while still covering the code that actually runs in production.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from agent_lens.control import ControlPlane
from agent_lens.models import EventType
from agent_lens.store import get_default_store
from agent_lens.tracer import Tracer

# Secrets planted in inputs to prove redaction happens on the capture path.
OPENAI_KEY = "sk-abcdefGHIJ1234567890"
ANTHROPIC_KEY = "sk-ant-abcdefGHIJ1234567890"


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _install_provider(monkeypatch, dotted_module: str, class_name: str, cls) -> None:
    """Register a fake provider module chain in sys.modules.

    e.g. dotted_module="openai.resources.chat.completions" builds every parent
    package so `import openai` and `from openai.resources.chat.completions
    import Completions` both resolve to our fakes. monkeypatch auto-restores.
    """
    parts = dotted_module.split(".")
    for i in range(len(parts)):
        name = ".".join(parts[: i + 1])
        mod = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, mod)
        if i > 0:
            setattr(sys.modules[".".join(parts[:i])], parts[i], mod)
    setattr(sys.modules[dotted_module], class_name, cls)


def _events_for_run(run_id: str):
    return get_default_store().get_events(run_id)


def _types(events):
    return [e.type for e in events]


def _all_blob(run_id) -> str:
    """JSON of every stored event's data for a run.

    Note: start_span() emits its own LLM_START event ({name,type}) separate
    from the integration's record_event(LLM_START, {...payload...}), so redaction
    must be asserted across the whole event stream, not one hand-picked event.
    """
    return json.dumps([e.data for e in _events_for_run(run_id)], default=str)


# ------------------------------------------------------------------
# OpenAI — Completions.create / .acreate monkey-patch
# ------------------------------------------------------------------

class _OAUsage:
    prompt_tokens = 11
    completion_tokens = 7


class _OAResp:
    usage = _OAUsage()

    def model_dump(self):
        return {"id": "cmpl-1", "choices": [{"message": {"role": "assistant", "content": "hi"}}]}


class _OACompletions:
    def create(self, *args, **kwargs):
        return _OAResp()


class _OAAsyncCompletions:
    """The SDK's async client is a separate class, not an `acreate` method."""

    async def create(self, *args, **kwargs):
        return _OAResp()


class _OABoom(_OACompletions):
    def create(self, *args, **kwargs):
        raise ValueError("upstream 500")


@pytest.fixture
def openai_patched(monkeypatch):
    """
    Install a fake `openai` and apply the agent-lens patch. Yields the class.

    Pass `async_cls` to also register the asynchronous call site: the SDK exposes
    it as `AsyncCompletions.create`, not as an `acreate` method on the sync class.
    """
    from agent_lens.integrations import openai as oai

    oai.unpatch()

    def _make(cls, async_cls=None):
        _install_provider(monkeypatch, "openai.resources.chat.completions", "Completions", cls)
        if async_cls is not None:
            sys.modules["openai.resources.chat.completions"].AsyncCompletions = async_cls
        assert oai.patch() is True
        return cls

    yield _make

    oai.unpatch()


def test_openai_records_start_and_end(openai_patched):
    Completions = openai_patched(_OACompletions)
    run = Tracer.get_instance().start_run("t")

    resp = Completions().create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"my key is {OPENAI_KEY}"}],
    )
    assert isinstance(resp, _OAResp)

    events = _events_for_run(run.id)
    assert EventType.LLM_START in _types(events)
    assert EventType.LLM_END in _types(events)

    end = next(e for e in events if e.type == EventType.LLM_END)
    assert end.data["provider"] == "openai"
    assert end.data["prompt_tokens"] == 11
    assert end.data["completion_tokens"] == 7
    assert end.data["total_tokens"] == 18
    assert end.data["cost_usd"] > 0  # gpt-4o is in the pricing table


def test_openai_redacts_secrets_in_capture(openai_patched):
    Completions = openai_patched(_OACompletions)
    run = Tracer.get_instance().start_run("t")

    Completions().create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"my key is {OPENAI_KEY}"}],
    )

    blob = _all_blob(run.id)
    assert OPENAI_KEY not in blob
    assert "[REDACTED]" in blob


def test_openai_error_path_marks_span_and_reraises(openai_patched):
    Completions = openai_patched(_OABoom)
    run = Tracer.get_instance().start_run("t")

    with pytest.raises(ValueError, match="upstream 500"):
        Completions().create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

    types_ = _types(_events_for_run(run.id))
    assert EventType.LLM_START in types_
    assert EventType.ERROR in types_          # end_span(status="error") emits ERROR
    assert EventType.LLM_END not in types_    # never reached


def test_openai_injection_bypasses_real_call(openai_patched):
    Completions = openai_patched(_OACompletions)
    tracer = Tracer.get_instance()
    run = tracer.start_run("t")

    sentinel = {"injected": True}
    ControlPlane.get_instance().inject(run.id, sentinel)

    resp = Completions().create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert resp is sentinel  # returned the injected result, not _OAResp

    # No span/events recorded because the call was short-circuited before start_span.
    assert EventType.LLM_START not in _types(_events_for_run(run.id))


async def test_openai_async_capture(openai_patched):
    openai_patched(_OACompletions, async_cls=_OAAsyncCompletions)
    run = Tracer.get_instance().start_run("t")

    resp = await _OAAsyncCompletions().create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert isinstance(resp, _OAResp)
    assert EventType.LLM_END in _types(_events_for_run(run.id))


# ------------------------------------------------------------------
# Anthropic — Messages.create monkey-patch
# ------------------------------------------------------------------

class _AntUsage:
    input_tokens = 100
    output_tokens = 40


class _AntThinkingBlock:
    type = "thinking"
    thinking = "internal reasoning"


class _AntResp:
    usage = _AntUsage()
    content = [_AntThinkingBlock()]

    def model_dump(self):
        return {"id": "msg-1", "content": [{"type": "text", "text": "hi"}]}


class _AntMessages:
    def create(self, *args, **kwargs):
        return _AntResp()


class _AntBoom:
    def create(self, *args, **kwargs):
        raise RuntimeError("overloaded")


@pytest.fixture
def anthropic_patched(monkeypatch):
    """
    Install a fake `anthropic` and apply the agent-lens patch.

    Pass `async_cls` to also register the asynchronous call site, which the SDK
    exposes as `AsyncMessages.create`.
    """
    from agent_lens.integrations import anthropic as ant

    ant.unpatch()

    def _make(cls, async_cls=None):
        _install_provider(monkeypatch, "anthropic.resources.messages", "Messages", cls)
        if async_cls is not None:
            sys.modules["anthropic.resources.messages"].AsyncMessages = async_cls
        assert ant.patch() is True
        return cls

    yield _make

    ant.unpatch()


def test_anthropic_records_tokens_cost_and_thinking(anthropic_patched):
    Messages = anthropic_patched(_AntMessages)
    run = Tracer.get_instance().start_run("t")

    Messages().create(
        model="claude-3-5-sonnet-20241022",
        system=f"you hold {ANTHROPIC_KEY}",
        messages=[{"role": "user", "content": "hello"}],
    )

    events = _events_for_run(run.id)
    assert EventType.LLM_START in _types(events)
    end = next(e for e in events if e.type == EventType.LLM_END)
    assert end.data["provider"] == "anthropic"
    assert end.data["input_tokens"] == 100
    assert end.data["output_tokens"] == 40
    assert end.data["cost_usd"] > 0
    # extended-thinking blocks are captured off the response
    assert end.data["thinking_blocks"] == ["internal reasoning"]


def test_anthropic_redacts_system_prompt(anthropic_patched):
    Messages = anthropic_patched(_AntMessages)
    run = Tracer.get_instance().start_run("t")

    Messages().create(
        model="claude-3-5-sonnet-20241022",
        system=f"you hold {ANTHROPIC_KEY}",
        messages=[{"role": "user", "content": "hello"}],
    )

    blob = _all_blob(run.id)
    assert ANTHROPIC_KEY not in blob
    assert "[REDACTED]" in blob


def test_anthropic_error_path_marks_span_and_reraises(anthropic_patched):
    Messages = anthropic_patched(_AntBoom)
    run = Tracer.get_instance().start_run("t")

    with pytest.raises(RuntimeError, match="overloaded"):
        Messages().create(model="claude-3-5-sonnet-20241022", messages=[{"role": "user", "content": "x"}])

    types_ = _types(_events_for_run(run.id))
    assert EventType.LLM_START in types_
    assert EventType.ERROR in types_
    assert EventType.LLM_END not in types_


# ------------------------------------------------------------------
# LangChain — AgentLensCallbackHandler
# ------------------------------------------------------------------

class _LCGen:
    text = "generated text"


class _LCResponse:
    llm_output = {"token_usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}
    generations = [[_LCGen()]]


def test_langchain_llm_callbacks_capture_and_redact(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")
    lc_run_id = uuid.uuid4()

    handler.on_llm_start(
        {"name": "gpt-4o"},
        [f"prompt containing {OPENAI_KEY}"],
        run_id=lc_run_id,
    )
    handler.on_llm_end(_LCResponse(), run_id=lc_run_id)

    events = _events_for_run(run.id)
    assert EventType.LLM_START in _types(events)
    end = next(e for e in events if e.type == EventType.LLM_END)
    assert end.data["provider"] == "langchain"
    assert end.data["total_tokens"] == 20
    assert end.data["generations"] == [[{"text": "generated text"}]]

    blob = _all_blob(run.id)
    assert OPENAI_KEY not in blob
    assert "[REDACTED]" in blob


def test_langchain_llm_error_records_error_event(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")
    lc_run_id = uuid.uuid4()

    handler.on_llm_start({"name": "gpt-4o"}, ["hi"], run_id=lc_run_id)
    handler.on_llm_error(ValueError("boom"), run_id=lc_run_id)

    error = next(e for e in _events_for_run(run.id) if e.type == EventType.ERROR)
    assert error.data["type"] == "ValueError"
    assert "boom" in error.data["error"]


def test_langchain_tool_callbacks_capture_and_redact(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")
    tool_run_id = uuid.uuid4()

    handler.on_tool_start({"name": "web_search"}, f"query with {OPENAI_KEY}", run_id=tool_run_id)
    handler.on_tool_end("3 results", run_id=tool_run_id)

    events = _events_for_run(run.id)
    assert EventType.TOOL_START in _types(events)
    assert EventType.TOOL_END in _types(events)
    blob = _all_blob(run.id)
    assert OPENAI_KEY not in blob
    assert "[REDACTED]" in blob


def test_langchain_chain_callbacks(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")
    chain_run_id = uuid.uuid4()

    handler.on_chain_start({"name": "qa_chain"}, {"question": "what is agent-lens?"}, run_id=chain_run_id)
    handler.on_chain_end({"answer": "an llm debugger"}, run_id=chain_run_id)

    types_ = _types(_events_for_run(run.id))
    assert EventType.AGENT_START in types_
    assert EventType.AGENT_END in types_


def test_langchain_handler_is_noop_when_unavailable(monkeypatch):
    """With langchain absent the handler must record nothing (graceful stub)."""
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", False)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")

    handler.on_llm_start({"name": "gpt-4o"}, ["hi"], run_id=uuid.uuid4())
    assert _events_for_run(run.id) == []


# ------------------------------------------------------------------
# LlamaIndex — AgentLensLlamaIndexHandler
# ------------------------------------------------------------------

class _FakeCBEventType:
    LLM = "llm"
    QUERY = "query"


class _FakeEventPayload:
    MESSAGES = "messages"
    SERIALIZED = "serialized"
    RESPONSE = "response"
    QUERY_STR = "query_str"


class _LIMessage:
    role = "user"

    def __init__(self, content):
        self.content = content


class _LIResponse:
    raw = {"usage": {"total_tokens": 21, "prompt_tokens": 13, "completion_tokens": 8}}
    message = "answer"


def test_llamaindex_llm_event_capture_and_redact(monkeypatch):
    from agent_lens.integrations import llamaindex as li

    # Construct while unavailable so __init__ skips the llama_index super().__init__,
    # then flip the module symbols to drive the real capture branches.
    handler = li.AgentLensLlamaIndexHandler()
    monkeypatch.setattr(li, "_LLAMAINDEX_AVAILABLE", True)
    monkeypatch.setattr(li, "CBEventType", _FakeCBEventType)
    monkeypatch.setattr(li, "EventPayload", _FakeEventPayload)

    run = Tracer.get_instance().start_run("t")

    handler.on_event_start(
        _FakeCBEventType.LLM,
        payload={
            _FakeEventPayload.MESSAGES: [_LIMessage(f"holds {OPENAI_KEY}")],
            _FakeEventPayload.SERIALIZED: {"model": "gpt-4o"},
        },
        event_id="e1",
    )
    handler.on_event_end(
        _FakeCBEventType.LLM,
        payload={_FakeEventPayload.RESPONSE: _LIResponse()},
        event_id="e1",
    )

    events = _events_for_run(run.id)
    assert EventType.LLM_START in _types(events)
    end = next(e for e in events if e.type == EventType.LLM_END)
    assert end.data["provider"] == "llamaindex"
    assert end.data["total_tokens"] == 21

    blob = _all_blob(run.id)
    assert OPENAI_KEY not in blob
    assert "[REDACTED]" in blob


def test_llamaindex_query_event_capture(monkeypatch):
    from agent_lens.integrations import llamaindex as li

    handler = li.AgentLensLlamaIndexHandler()
    monkeypatch.setattr(li, "_LLAMAINDEX_AVAILABLE", True)
    monkeypatch.setattr(li, "CBEventType", _FakeCBEventType)
    monkeypatch.setattr(li, "EventPayload", _FakeEventPayload)

    run = Tracer.get_instance().start_run("t")

    handler.on_event_start(
        _FakeCBEventType.QUERY,
        payload={_FakeEventPayload.QUERY_STR: "what is agent-lens?"},
        event_id="q1",
    )
    handler.on_event_end(
        _FakeCBEventType.QUERY,
        payload={_FakeEventPayload.RESPONSE: "an llm debugger"},
        event_id="q1",
    )

    types_ = _types(_events_for_run(run.id))
    assert EventType.AGENT_START in types_
    assert EventType.AGENT_END in types_


# ==================================================================
# Deeper coverage: pure helpers, async paths, error/guard branches,
# graceful no-ops, and unpatch().
# ==================================================================

# ---- openai pure helpers -----------------------------------------

def test_openai_estimate_cost_known_and_unknown_model():
    from agent_lens.integrations import openai as oai

    assert oai._estimate_cost("gpt-4o", 1000, 1000) > 0
    assert oai._estimate_cost("gpt-4o-2024-11-20", 1000, 1000) > 0  # substring match
    assert oai._estimate_cost("some-unlisted-model", 1000, 1000) == 0.0


def test_openai_extract_messages_variants():
    from agent_lens.integrations import openai as oai

    class _Model:
        def model_dump(self):
            return {"role": "assistant", "content": "ok"}

    class _BadModel:
        def model_dump(self):
            raise ValueError("nope")

    class _NoDump:
        pass

    out = oai._extract_messages(
        [{"role": "user", "content": f"k {OPENAI_KEY}", "function_call": {"x": 1}}]
    )
    assert out[0]["role"] == "user"
    assert "function_call" not in out[0]  # stripped
    assert OPENAI_KEY not in json.dumps(out, default=str)  # redacted

    assert oai._extract_messages([_Model()])[0]["content"] == "ok"
    assert oai._extract_messages([_BadModel()])[0]["role"] == "unknown"  # except branch
    assert oai._extract_messages([_NoDump()])[0]["role"] == "unknown"    # dict() fails -> except
    assert oai._extract_messages(None) == []
    assert oai._extract_messages("not-a-list") == []


def test_openai_safe_response_dict_variants():
    from agent_lens.integrations import openai as oai

    class _WithDump:
        def model_dump(self):
            return {"tok": f"leak {OPENAI_KEY}"}

    class _WithDict:
        def __init__(self):
            self.public = 1
            self._private = 2

    d = oai._safe_response_dict(_WithDump())
    assert OPENAI_KEY not in json.dumps(d, default=str)  # redacted

    d2 = oai._safe_response_dict(_WithDict())
    assert d2.get("public") == 1
    assert "_private" not in d2  # underscored keys dropped

    assert oai._safe_response_dict(object()) == {}  # no dump / no __dict__ -> {}


# ---- anthropic pure helpers --------------------------------------

def test_anthropic_estimate_cost_known_and_unknown():
    from agent_lens.integrations import anthropic as ant

    assert ant._estimate_cost("claude-3-opus-20240229", 1_000_000, 0) > 0
    assert ant._estimate_cost("mystery-model", 100, 100) == 0.0


def test_anthropic_helpers_extract_and_thinking():
    from agent_lens.integrations import anthropic as ant

    class _Think:
        type = "thinking"
        thinking = "internal"

    class _Text:
        type = "text"

    class _Resp:
        content = [_Think(), _Text()]

    assert ant._extract_thinking_blocks(_Resp()) == ["internal"]
    assert ant._extract_thinking_blocks(object()) == []  # no content -> []

    out = ant._extract_messages([{"role": "user", "content": f"k {ANTHROPIC_KEY}"}])
    assert ANTHROPIC_KEY not in json.dumps(out, default=str)
    assert ant._extract_messages("not-a-list") == []


# ---- anthropic async capture -------------------------------------

class _AntMessagesAsync:
    """The SDK's async client is `AsyncMessages`, with a `create` coroutine."""

    async def create(self, *args, **kwargs):
        return _AntResp()


async def test_anthropic_async_capture(anthropic_patched):
    anthropic_patched(_AntMessages, async_cls=_AntMessagesAsync)
    run = Tracer.get_instance().start_run("t")

    resp = await _AntMessagesAsync().create(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert isinstance(resp, _AntResp)
    end = next(e for e in _events_for_run(run.id) if e.type == EventType.LLM_END and "provider" in e.data)
    assert end.data["input_tokens"] == 100


# ---- openai async injection + error ------------------------------

class _OABoomAsync:
    async def create(self, *args, **kwargs):
        raise ValueError("async upstream 500")


async def test_openai_async_injection_bypasses_call(openai_patched):
    openai_patched(_OACompletions, async_cls=_OAAsyncCompletions)
    run = Tracer.get_instance().start_run("t")

    sentinel = {"injected": True}
    ControlPlane.get_instance().inject(run.id, sentinel)

    resp = await _OAAsyncCompletions().create(model="gpt-4o", messages=[])
    assert resp is sentinel
    assert EventType.LLM_START not in _types(_events_for_run(run.id))


async def test_openai_async_error_path(openai_patched):
    openai_patched(_OACompletions, async_cls=_OABoomAsync)
    run = Tracer.get_instance().start_run("t")

    with pytest.raises(ValueError, match="async upstream 500"):
        await _OABoomAsync().create(model="gpt-4o", messages=[])

    types_ = _types(_events_for_run(run.id))
    assert EventType.LLM_START in types_
    assert EventType.ERROR in types_


# ---- unpatch() ---------------------------------------------------

def test_openai_unpatch_resets_flag(openai_patched):
    openai_patched(_OACompletions)
    from agent_lens.integrations import openai as oai

    assert oai._patched is True
    oai.unpatch()
    assert oai._patched is False


def test_anthropic_unpatch_resets_flag(anthropic_patched):
    anthropic_patched(_AntMessages)
    from agent_lens.integrations import anthropic as ant

    assert ant._patched is True
    ant.unpatch()
    assert ant._patched is False


# ---- langchain error paths + span guard --------------------------

def test_langchain_tool_and_chain_error_record_error(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")

    tool_id = uuid.uuid4()
    handler.on_tool_start({"name": "search"}, "q", run_id=tool_id)
    handler.on_tool_error(ValueError("tool boom"), run_id=tool_id)

    chain_id = uuid.uuid4()
    handler.on_chain_start({"name": "qa"}, {"q": "x"}, run_id=chain_id)
    handler.on_chain_error(RuntimeError("chain boom"), run_id=chain_id)

    errors = [e for e in _events_for_run(run.id) if e.type == EventType.ERROR]
    kinds = {e.data.get("type") for e in errors}
    assert "ValueError" in kinds
    assert "RuntimeError" in kinds


def test_langchain_unknown_span_is_ignored(monkeypatch):
    import uuid

    from agent_lens.integrations import langchain as lc

    monkeypatch.setattr(lc, "_LANGCHAIN_AVAILABLE", True)
    handler = lc.AgentLensCallbackHandler()
    run = Tracer.get_instance().start_run("t")

    # on_llm_end for a run_id that never started must be a safe no-op.
    handler.on_llm_end(_LCResponse(), run_id=uuid.uuid4())
    assert EventType.LLM_END not in _types(_events_for_run(run.id))


# ---- llamaindex graceful no-op + trace hooks ---------------------

def test_llamaindex_noop_when_unavailable(monkeypatch):
    from agent_lens.integrations import llamaindex as li

    # Force the unavailable path rather than inferring it from the environment:
    # this test used to rely on llama-index simply not being installed, so it
    # inverted and failed in any job that does install it. The hooks read the
    # flag at call time, so flipping it is enough.
    monkeypatch.setattr(li, "_LLAMAINDEX_AVAILABLE", False)

    handler = li.AgentLensLlamaIndexHandler()
    run = Tracer.get_instance().start_run("t")

    assert handler.on_event_start("llm", payload={}, event_id="e1") == "e1"
    handler.on_event_end("llm", payload={}, event_id="e1")
    handler.start_trace("trace-1")
    handler.end_trace("trace-1", {})

    assert _events_for_run(run.id) == []
