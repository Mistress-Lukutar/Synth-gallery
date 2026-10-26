'''
File:   helpers.py
Brief:  Shared helpers for LLM client unit tests (MockTransport plumbing).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable

import httpx

from app.infrastructure.services.llm.types import ChatRequest, LLMClient


async def collect_events(client: LLMClient, request: ChatRequest) -> list:
    '''Run ``client.stream_chat`` and return every event as a list.'''
    events: list = []
    async for event in client.stream_chat(request):
        events.append(event)
    return events


def make_stream_transport(
    sse: bytes | list[bytes],
    captured: dict[str, Any],
) -> Callable[[httpx.Request], httpx.Response]:
    '''Build a MockTransport handler serving the given SSE bytes.

    The body is served through an async generator so it stays streaming
    (MockTransport buffers sync-iterator bodies into a single chunk, which
    would hide chunk-boundary bugs). The request is stored into
    ``captured["request"]`` for assertions.
    '''
    chunks: list[bytes] = [sse] if isinstance(sse, bytes) else list(sse)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request

        async def body() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return httpx.Response(200, content=body())

    return handler


def make_json_transport(
    payload: Any = None,
    status_code: int = 200,
) -> Callable[[httpx.Request], httpx.Response]:
    '''Build a MockTransport handler returning a JSON response.'''

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload if payload is not None else {})

    return handler


def sse_frames(*frames: str) -> bytes:
    '''Encode SSE frames (each string is one frame; a blank line is appended).'''
    return "".join(frame + "\n\n" for frame in frames).encode("utf-8")


def request_json(captured: dict[str, Any]) -> dict:
    '''Decode the JSON body of the captured request.'''
    return json.loads(captured["request"].content)
