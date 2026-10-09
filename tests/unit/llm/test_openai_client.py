'''
File:   test_openai_client.py
Brief:  Unit tests for the OpenAI-compatible client (via httpx.MockTransport).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import httpx
import pytest

from app.infrastructure.services.llm import (
    AiProtocol,
    ChatRequest,
    Endpoint,
    ImagePart,
    LLMError,
    StopReason,
    StreamError,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallsReady,
    ToolResult,
    ToolSpec,
    Turn,
    TurnComplete,
    get_llm_client,
)
from tests.unit.llm.helpers import (
    collect_events,
    make_json_transport,
    make_stream_transport,
    request_json,
)

BASE_URL = "https://api.example.com/v1"

STOP_SSE = (
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    b'data: [DONE]\n\n'
)


def _request(turns, tools=None, max_tokens=None, system="You are helpful."):
    return ChatRequest(
        endpoint=Endpoint(BASE_URL, "sk-test", "gpt-4o", AiProtocol.OPENAI_COMPATIBLE),
        system_prompt=system,
        turns=list(turns),
        tools=list(tools or []),
        max_tokens=max_tokens,
    )


def _client(captured, sse):
    transport = httpx.MockTransport(make_stream_transport(sse, captured))
    return get_llm_client(AiProtocol.OPENAI_COMPATIBLE, transport=transport)


async def test_request_body_shape():
    captured: dict = {}
    client = _client(captured, STOP_SSE)
    tools = [
        ToolSpec(
            "get_weather",
            "Get current weather",
            {"type": "object", "properties": {"city": {"type": "string"}}},
        )
    ]
    request = _request(
        [Turn("user", [TextPart("What is this?"), ImagePart("image/png", "AAAA")])],
        tools=tools,
    )
    events = await collect_events(client, request)

    req = captured["request"]
    assert req.headers["authorization"] == "Bearer sk-test"
    body = request_json(captured)
    assert body["model"] == "gpt-4o"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["temperature"] == 0.7
    assert "max_tokens" not in body
    assert body["messages"][0] == {"role": "system", "content": "You are helpful."}
    user = body["messages"][1]
    assert user["role"] == "user"
    assert {"type": "text", "text": "What is this?"} in user["content"]
    image_part = user["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"] == "data:image/png;base64,AAAA"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]
    assert events == [TurnComplete(StopReason.END_TURN, None, None)]


async def test_max_tokens_included_only_when_set():
    captured: dict = {}
    client = _client(captured, STOP_SSE)
    await collect_events(client, _request([], max_tokens=512))
    assert request_json(captured)["max_tokens"] == 512


async def test_assistant_and_tool_message_mapping():
    captured: dict = {}
    client = _client(captured, STOP_SSE)
    turns = [
        Turn("user", [TextPart("Weather?")]),
        Turn("assistant", [ToolCall("call_9", "get_weather", '{"city": "Rome"}')]),
        Turn("tool", [ToolResult("call_9", "get_weather", '{"temp": 30}')]),
    ]
    await collect_events(client, _request(turns))
    messages = request_json(captured)["messages"]
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] is None
    assert messages[2]["tool_calls"] == [
        {
            "id": "call_9",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Rome"}'},
        }
    ]
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_9",
        "content": '{"temp": 30}',
    }


async def test_assistant_text_and_tool_calls_together():
    captured: dict = {}
    client = _client(captured, STOP_SSE)
    turns = [
        Turn(
            "assistant",
            [TextPart("Let me check."), ToolCall("c1", "f", "{}")],
        ),
    ]
    await collect_events(client, _request(turns, system=None))
    assistant = request_json(captured)["messages"][0]
    assert assistant["content"] == "Let me check."
    assert len(assistant["tool_calls"]) == 1


async def test_fragmented_tool_call_deltas_accumulate():
    captured: dict = {}
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        b'"type":"function","function":{"name":"get_weather","arguments":""}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
        b'{"arguments":"{\\"city\\":"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
        b'{"arguments":" \\"Rome\\"}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7}}\n\n'
        b'data: [DONE]\n\n'
    )
    # Split at awkward byte boundaries to exercise parser buffering.
    chunks = [sse[i : i + 37] for i in range(0, len(sse), 37)]
    client = _client(captured, chunks)
    events = await collect_events(client, _request([Turn("user", [TextPart("Hi")])]))

    assert events == [
        TextDelta("Hel"),
        ToolCallsReady(
            tool_calls=[ToolCall("call_1", "get_weather", '{"city": "Rome"}')],
            text="Hel",
        ),
        TurnComplete(StopReason.TOOL_USE, 11, 7),
    ]


async def test_text_delta_order_and_usage_extraction():
    captured: dict = {}
    sse = (
        b'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"C"},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
        b'data: [DONE]\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))

    assert [type(e).__name__ for e in events] == [
        "TextDelta", "ReasoningDelta", "TextDelta", "TextDelta", "TurnComplete",
    ]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["A", "B", "C"]
    assert events[-1] == TurnComplete(StopReason.END_TURN, 5, 3)


async def test_done_terminates_stream_without_extra_events():
    captured: dict = {}
    sse = (
        b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n'
        b'data: {"choices":[{"delta":{"content":"after-done"}}]}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [TextDelta("x"), TurnComplete(StopReason.END_TURN, None, None)]


async def test_stream_without_finish_reason_yields_stream_error():
    captured: dict = {}
    sse = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n'
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [TextDelta("x"), StreamError("Stream ended unexpectedly")]


async def test_http_error_yields_stream_error_with_provider_message():
    transport = httpx.MockTransport(
        make_json_transport({"error": {"message": "Rate limit reached"}}, status_code=429)
    )
    client = get_llm_client(AiProtocol.OPENAI_COMPATIBLE, transport=transport)
    events = await collect_events(client, _request([]))
    assert events == [StreamError("Rate limit reached", 429)]


async def test_fetch_models_parses_data_and_limits():
    payload = {
        "data": [
            {"id": "gpt-4o", "context_length": 128000},
            {
                "id": "meta-llama/llama-3-70b-instruct",
                "top_provider": {
                    "context_length": 8192,
                    "max_completion_tokens": 4096,
                },
            },
            {"id": "text-embedding-3-small", "max_completion_tokens": 2048},
        ]
    }
    transport = httpx.MockTransport(make_json_transport(payload))
    client = get_llm_client(AiProtocol.OPENAI_COMPATIBLE, transport=transport)
    models = await client.fetch_models(BASE_URL, "sk-test")

    assert [m.model_id for m in models] == [
        "gpt-4o",
        "meta-llama/llama-3-70b-instruct",
        "text-embedding-3-small",
    ]
    assert models[0].display_name == "gpt-4o"
    assert models[0].context_tokens == 128000
    assert models[0].max_output_tokens is None
    assert (models[0].supports_tools, models[0].supports_vision) == (True, True)
    assert models[1].context_tokens == 8192
    assert models[1].max_output_tokens == 4096
    assert models[2].supports_tools is False
    assert models[2].max_output_tokens == 2048


async def test_fetch_models_http_error_raises_llm_error():
    transport = httpx.MockTransport(
        make_json_transport({"error": {"message": "Incorrect API key"}}, status_code=401)
    )
    client = get_llm_client(AiProtocol.OPENAI_COMPATIBLE, transport=transport)
    with pytest.raises(LLMError) as excinfo:
        await client.fetch_models(BASE_URL, "sk-test")
    assert excinfo.value.http_code == 401
    assert "Incorrect API key" in str(excinfo.value)
