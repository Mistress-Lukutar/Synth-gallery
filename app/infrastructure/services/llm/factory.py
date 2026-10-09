'''
File:   factory.py
Brief:  Shared HTTP client and per-protocol LLM client factory.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

from typing import Optional

import httpx

from app.infrastructure.services.llm.anthropic_client import AnthropicClient
from app.infrastructure.services.llm.gemini_client import GeminiClient
from app.infrastructure.services.llm.openai_client import OpenAiClient
from app.infrastructure.services.llm.types import AiProtocol, LLMClient

# Timeouts tuned for LLM workloads: thinking models can be slow to first
# byte, generations are long-lived read streams, and prompt writes (inline
# images) can be large.
LLM_HTTP_TIMEOUT = httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)

_shared_client: Optional[httpx.AsyncClient] = None


def get_shared_http_client() -> httpx.AsyncClient:
    '''Return the lazily created process-wide async HTTP client.'''
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=LLM_HTTP_TIMEOUT)
    return _shared_client


def get_llm_client(
    protocol: AiProtocol,
    transport: Optional[httpx.AsyncTransport] = None,
) -> LLMClient:
    '''Return the client implementing the given provider protocol.

    Args:
        protocol: Target provider protocol.
        transport: Optional httpx transport override (used by tests to
            inject ``httpx.MockTransport``). When given, a dedicated
            ``httpx.AsyncClient`` is created for the returned client;
            otherwise the lazily shared client is used.

    Returns:
        An ``OpenAiClient`` / ``AnthropicClient`` / ``GeminiClient``.
    '''
    if transport is not None:
        http = httpx.AsyncClient(transport=transport, timeout=LLM_HTTP_TIMEOUT)
    else:
        http = get_shared_http_client()
    if protocol == AiProtocol.ANTHROPIC:
        return AnthropicClient(http)
    if protocol == AiProtocol.GOOGLE_GEMINI:
        return GeminiClient(http)
    return OpenAiClient(http)
