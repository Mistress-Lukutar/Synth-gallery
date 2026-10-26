'''
File:   test_anthropic_client.py
Brief:  Unit tests for the Anthropic client (via httpx.MockTransport).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import httpx
import pytest

from app.infrastructure.services.llm import (
    AiProtocol,
    AnthropicClient,
    ChatRequest,
    Endpoint,
    ImagePart,
    LLMError,
    ReasoningDelta,
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

BASE_URL = "https://api.anthropic.com/v1"


def _request(turns, tools=None, max_tokens=None, system="Be brief."):
    return ChatRequest(
        endpoint=Endpoint(
            BASE_URL, "ak-test", "claude-3-5-sonnet-20241022", AiProtocol.ANTHROPIC
        ),
        system_prompt=system,
        turns=list(turns),
        tools=list(tools or []),
        max_tokens=max_tokens,
    )


def _client(captured, sse):
    transport = httpx.MockTransport(make_stream_transport(sse, captured))
    return get_llm_client(AiProtocol.ANTHROPIC, transport=transport)


async def test_request_shape_and_default_max_tokens():
    captured: dict = {}
    sse = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    client = _client(captured, sse)
    tools = [
        ToolSpec(
            "get_weather",
            "Get current weather",
            {"type": "object", "properties": {"city": {"type": "string"}}},
        )
    ]
    turns = [
        Turn("user", [TextPart("Hello"), ImagePart("image/png", "AAAA")]),
        Turn("assistant", [ToolCall("toolu_9", "get_weather", '{"city": "Paris"}')]),
        Turn("tool", [ToolResult("toolu_9", "get_weather", '{"temp": 21}')]),
        Turn("tool", [ToolResult("toolu_10", "get_time", '{"time": "10:00"}')]),
    ]
    await collect_events(client, _request(turns, tools=tools))

    req = captured["request"]
    assert req.headers["x-api-key"] == "ak-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = request_json(captured)
    assert body["model"] == "claude-3-5-sonnet-20241022"
    assert body["max_tokens"] == 8192  # mandatory for Anthropic
    assert body["stream"] is True
    assert body["system"] == "Be brief."
    assert body["temperature"] == 0.7
    assert body["tools"] == [
        {
            "name": "get_weather",
            "description": "Get current weather",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]
    messages = body["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0] == {"type": "text", "text": "Hello"}
    assert messages[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    assert messages[1] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_9",
                "name": "get_weather",
                "input": {"city": "Paris"},
            }
        ],
    }
    # Two consecutive tool turns fold into ONE user message.
    assert len(messages) == 3
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_9", "content": '{"temp": 21}'},
        {"type": "tool_result", "tool_use_id": "toolu_10", "content": '{"time": "10:00"}'},
    ]


async def test_max_tokens_override():
    captured: dict = {}
    sse = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    client = _client(captured, sse)
    await collect_events(client, _request([], max_tokens=256))
    assert request_json(captured)["max_tokens"] == 256


async def test_tool_result_images_appended_to_same_user_message():
    captured: dict = {}
    sse = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    client = _client(captured, sse)
    turns = [
        Turn("user", [TextPart("Hi")]),
        Turn("assistant", [ToolCall("t1", "shot", "{}")]),
        Turn(
            "tool",
            [ToolResult("t1", "shot", "ok", images=[ImagePart("image/jpeg", "BB")])],
        ),
    ]
    await collect_events(client, _request(turns))
    messages = request_json(captured)["messages"]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "BB"},
        },
    ]


async def test_stream_events_and_tool_json_accumulation():
    captured: dict = {}
    sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":42}}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_1","name":"get_weather"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":" \\"Berlin\\"}"}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":17}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([Turn("user", [TextPart("Hi")])]))

    assert events == [
        TextDelta("Hello"),
        ReasoningDelta("hmm"),
        ToolCallsReady(
            tool_calls=[ToolCall("toolu_1", "get_weather", '{"city": "Berlin"}')],
            text="Hello",
        ),
        TurnComplete(StopReason.TOOL_USE, 42, 17),
    ]


async def test_stop_reason_end_turn_mapping():
    captured: dict = {}
    sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [TextDelta("Hi"), TurnComplete(StopReason.END_TURN, 5, 2)]


async def test_stop_reason_max_tokens_maps_to_length():
    captured: dict = {}
    sse = (
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":8192}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events[-1] == TurnComplete(StopReason.LENGTH, None, 8192)


async def test_error_event_yields_stream_error():
    captured: dict = {}
    sse = (
        b'event: error\n'
        b'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [StreamError("Overloaded")]


async def test_http_error_yields_stream_error_with_provider_message():
    transport = httpx.MockTransport(
        make_json_transport(
            {
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
            status_code=401,
        )
    )
    client = get_llm_client(AiProtocol.ANTHROPIC, transport=transport)
    events = await collect_events(client, _request([]))
    assert events == [StreamError("invalid x-api-key", 401)]


async def test_fetch_models_parses_data_and_display_names():
    payload = {
        "data": [
            {"id": "claude-3-5-sonnet-20241022", "display_name": "Claude 3.5 Sonnet"},
            {"id": "claude-opus-4-1"},
        ]
    }
    transport = httpx.MockTransport(make_json_transport(payload))
    client = get_llm_client(AiProtocol.ANTHROPIC, transport=transport)
    models = await client.fetch_models(BASE_URL, "ak-test")

    assert [m.model_id for m in models] == [
        "claude-3-5-sonnet-20241022",
        "claude-opus-4-1",
    ]
    assert models[0].display_name == "Claude 3.5 Sonnet"
    assert models[1].display_name == "claude-opus-4-1"  # falls back to id
    assert (models[0].supports_tools, models[0].supports_vision) == (True, True)


async def test_fetch_models_http_error_raises_llm_error():
    transport = httpx.MockTransport(
        make_json_transport(
            {"error": {"message": "invalid x-api-key"}}, status_code=401
        )
    )
    client = get_llm_client(AiProtocol.ANTHROPIC, transport=transport)
    with pytest.raises(LLMError) as excinfo:
        await client.fetch_models(BASE_URL, "ak-test")
    assert excinfo.value.http_code == 401
    assert isinstance(client, AnthropicClient)
