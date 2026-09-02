"""
Fixtures for the end-to-end provider suites.

These tests used respx to mock the vendor HTTP endpoints, and respx had silently
stopped working. The reason is worth recording: **openai 3.7.0 and anthropic
1.3.0 both build their clients on `httpx2`, while respx patches `httpx`.** respx
was not failing to match a route — it was instrumenting a library the SDKs no
longer use, so every mock was an inert no-op and every request went to the real
API. Passing an `httpx.Client` to either SDK is now a hard `TypeError`
("Expected an instance of `httpx2.Client`"), which is the same migration seen
from the other side.

It went unnoticed because the suite also skipped whenever the provider SDK was
absent, and the `dev` extra does not install the providers — so it was skipping
by accident rather than by intent, and would have made billable live calls for
anyone with a real key exported.

Both SDKs accept an explicit `http_client`, so the mock is installed on the
client under test rather than patched into a library globally. Nothing leaves the
machine, and this cannot silently degrade the same way: a transport that is not
used produces no response at all, rather than a real one. Each suite also carries
a positive control asserting the mocked transport actually received the request.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

# The SDKs moved to httpx2; fall back to httpx so the helper still works against
# older SDK majors rather than failing to import.
try:
    import httpx2 as sdk_httpx
except ImportError:  # pragma: no cover - exercised only on pre-httpx2 SDKs
    import httpx as sdk_httpx


class RecordingTransport:
    """
    An httpx MockTransport that answers from a fixed payload and records requests.

    Recording the requests is what makes "no API key was leaked" assertions
    meaningful: the test can inspect exactly what would have gone over the wire.
    """

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.requests: list[sdk_httpx.Request] = []

    def __call__(self, request: sdk_httpx.Request) -> sdk_httpx.Response:
        self.requests.append(request)
        return sdk_httpx.Response(self.status, json=self.payload)

    @property
    def transport(self) -> sdk_httpx.MockTransport:
        return sdk_httpx.MockTransport(self)

    def sent_bodies(self) -> str:
        return json.dumps(
            [json.loads(r.content) if r.content else {} for r in self.requests]
        )


@pytest.fixture
def mock_openai():
    """
    Return a factory: (payload, status=200) -> (client, transport).

    The client is a real `openai.OpenAI` with a mocked transport, so the SDK's own
    request building, serialisation and error handling all run for real.
    """
    openai = pytest.importorskip("openai", reason="openai SDK not installed")

    def _make(payload: dict[str, Any], status: int = 200, api_key: str = "sk-test-0000000000"):
        recorder = RecordingTransport(payload, status)
        client = openai.OpenAI(
            api_key=api_key,
            http_client=sdk_httpx.Client(transport=recorder.transport),
            max_retries=0,
        )
        return client, recorder

    return _make


@pytest.fixture
def mock_anthropic():
    """Return a factory: (payload, status=200) -> (client, transport)."""
    anthropic = pytest.importorskip("anthropic", reason="anthropic SDK not installed")

    def _make(payload: dict[str, Any], status: int = 200, api_key: str = "sk-ant-test-0000000000"):
        recorder = RecordingTransport(payload, status)
        client = anthropic.Anthropic(
            api_key=api_key,
            http_client=sdk_httpx.Client(transport=recorder.transport),
            max_retries=0,
        )
        return client, recorder

    return _make
