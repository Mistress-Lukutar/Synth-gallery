'''
File:   test_factory.py
Brief:  Unit tests for the LLM client factory.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import httpx

from app.infrastructure.services.llm import (
    AiProtocol,
    AnthropicClient,
    GeminiClient,
    OpenAiClient,
    get_llm_client,
)
from app.infrastructure.services.llm.factory import (
    LLM_HTTP_TIMEOUT,
    get_shared_http_client,
)


def _mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={}))


def test_get_llm_client_returns_matching_class():
    transport = _mock_transport()
    assert isinstance(
        get_llm_client(AiProtocol.OPENAI_COMPATIBLE, transport=transport), OpenAiClient
    )
    assert isinstance(
        get_llm_client(AiProtocol.ANTHROPIC, transport=transport), AnthropicClient
    )
    assert isinstance(
        get_llm_client(AiProtocol.GOOGLE_GEMINI, transport=transport), GeminiClient
    )


def test_unknown_protocol_falls_back_to_openai_compatible():
    client = get_llm_client("something-else", transport=_mock_transport())
    assert isinstance(client, OpenAiClient)


def test_shared_http_client_is_lazy_singleton():
    first = get_shared_http_client()
    second = get_shared_http_client()
    assert first is second
    assert first.timeout == LLM_HTTP_TIMEOUT
    assert LLM_HTTP_TIMEOUT.connect == 20.0
    assert LLM_HTTP_TIMEOUT.read == 120.0
    assert LLM_HTTP_TIMEOUT.write == 60.0
    assert LLM_HTTP_TIMEOUT.pool == 20.0
