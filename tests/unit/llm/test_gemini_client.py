'''
File:   test_gemini_client.py
Brief:  Unit tests for the Google Gemini client (via httpx.MockTransport).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import json

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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL_ID = "gemini-2.0-flash"


def _request(turns, tools=None, max_tokens=None, system="Be nice."):
    return ChatRequest(
        endpoint=Endpoint(BASE_URL, "gkey", MODEL_ID, AiProtocol.GOOGLE_GEMINI),
        system_prompt=system,
        turns=list(turns),
        tools=list(tools or []),
        max_tokens=max_tokens,
    )


def _client(captured, sse):
    transport = httpx.MockTransport(make_stream_transport(sse, captured))
    return get_llm_client(AiProtocol.GOOGLE_GEMINI, transport=transport)


async def test_url_headers_and_body_shape():
    captured: dict = {}
    sse = (
        b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}],"role":"model"},'
        b'"finishReason":"STOP"}]}\n\n'
    )
    client = _client(captured, sse)
    tools = [
        ToolSpec(
            "get_weather",
            "Get current weather",
            {"type": "object", "properties": {"city": {"type": "string"}}},
        )
    ]
    turns = [
        Turn("user", [TextPart("Hi"), ImagePart("image/jpeg", "BB")]),
        Turn(
            "assistant",
            [TextPart("Checking"), ToolCall("ignored", "get_weather", '{"city": "Rome"}')],
        ),
        Turn("tool", [ToolResult("ignored", "get_weather", '{"temp": 25}')]),
        Turn("tool", [ToolResult("ignored2", "other_tool", "done")]),
    ]
    events = await collect_events(client, _request(turns, tools=tools, max_tokens=512))

    req = captured["request"]
    assert req.url.path.endswith(f"/models/{MODEL_ID}:streamGenerateContent")
    assert req.url.params["alt"] == "sse"
    assert req.headers["x-goog-api-key"] == "gkey"

    body = request_json(captured)
    assert body["systemInstruction"] == {"parts": [{"text": "Be nice."}]}
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                }
            ]
        }
    ]
    assert body["generationConfig"] == {"temperature": 0.7, "maxOutputTokens": 512}

    contents = body["contents"]
    assert contents[0] == {
        "role": "user",
        "parts": [
            {"text": "Hi"},
            {"inlineData": {"mimeType": "image/jpeg", "data": "BB"}},
        ],
    }
    assert contents[1] == {
        "role": "model",
        "parts": [
            {"text": "Checking"},
            {"functionCall": {"name": "get_weather", "args": {"city": "Rome"}}},
        ],
    }
    # Two consecutive tool turns fold into one user content.
    assert contents[2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "get_weather",
                    "response": {"result": '{"temp": 25}'},
                }
            },
            {"functionResponse": {"name": "other_tool", "response": {"result": "done"}}},
        ],
    }
    assert len(contents) == 3
    assert events == [TextDelta("ok"), TurnComplete(StopReason.END_TURN, None, None)]


async def test_max_output_tokens_omitted_when_none():
    captured: dict = {}
    sse = (
        b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}],"role":"model"},'
        b'"finishReason":"STOP"}]}\n\n'
    )
    client = _client(captured, sse)
    await collect_events(client, _request([], max_tokens=None))
    assert request_json(captured)["generationConfig"] == {"temperature": 0.7}


async def test_stream_synthesizes_call_ids_and_usage():
    captured: dict = {}
    sse = (
        b'data: {"candidates":[{"content":{"parts":[{"text":"Checking"}],"role":"model"}}]}\n\n'
        b'data: {"candidates":[{"content":{"parts":['
        b'{"functionCall":{"name":"a","args":{"x":1}}},'
        b'{"functionCall":{"name":"b","args":{}}}'
        b'],"role":"model"}}]}\n\n'
        b'data: {"candidates":[{"content":{"role":"model","parts":[]},"finishReason":"STOP"}],'
        b'"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([Turn("user", [TextPart("Hi")])]))

    assert events[0] == TextDelta("Checking")
    ready = events[1]
    assert isinstance(ready, ToolCallsReady)
    assert ready.text == "Checking"
    assert [c.call_id for c in ready.tool_calls] == ["call_1", "call_2"]
    assert [c.name for c in ready.tool_calls] == ["a", "b"]
    assert json.loads(ready.tool_calls[0].arguments_json) == {"x": 1}
    assert json.loads(ready.tool_calls[1].arguments_json) == {}
    assert events[2] == TurnComplete(StopReason.END_TURN, 10, 5)


async def test_finish_reason_mapping():
    captured: dict = {}
    sse = (
        b'data: {"candidates":[{"content":{"role":"model","parts":[]},'
        b'"finishReason":"MAX_TOKENS"}],'
        b'"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":9}}\n\n'
    )
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [TurnComplete(StopReason.LENGTH, 3, 9)]


async def test_error_frame_yields_stream_error():
    captured: dict = {}
    sse = b'data: {"error":{"code":400,"message":"API key not valid","status":"INVALID_ARGUMENT"}}\n\n'
    client = _client(captured, sse)
    events = await collect_events(client, _request([]))
    assert events == [StreamError("API key not valid", 400)]


async def test_http_error_yields_stream_error_with_provider_message():
    transport = httpx.MockTransport(
        make_json_transport(
            {"error": {"code": 403, "message": "Permission denied"}}, status_code=403
        )
    )
    client = get_llm_client(AiProtocol.GOOGLE_GEMINI, transport=transport)
    events = await collect_events(client, _request([]))
    assert events == [StreamError("Permission denied", 403)]


async def test_fetch_models_filters_and_strips_prefix():
    payload = {
        "models": [
            {
                "name": "models/gemini-1.5-pro",
                "displayName": "Gemini 1.5 Pro",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {
                "name": "models/text-embedding-004",
                "displayName": "Text Embedding 004",
                "supportedGenerationMethods": ["embedContent"],
            },
            {
                "name": "models/gemini-1.5-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
            {"name": "tunedModels/my-tuning", "supportedGenerationMethods": []},
        ]
    }
    transport = httpx.MockTransport(make_json_transport(payload))
    client = get_llm_client(AiProtocol.GOOGLE_GEMINI, transport=transport)
    models = await client.fetch_models(BASE_URL, "gkey")

    assert [m.model_id for m in models] == ["gemini-1.5-pro", "gemini-1.5-flash"]
    assert models[0].display_name == "Gemini 1.5 Pro"
    assert models[1].display_name == "gemini-1.5-flash"  # falls back to id
    assert (models[0].supports_tools, models[0].supports_vision) == (True, True)


async def test_fetch_models_http_error_raises_llm_error():
    transport = httpx.MockTransport(
        make_json_transport(
            {"error": {"code": 400, "message": "API key not valid"}}, status_code=400
        )
    )
    client = get_llm_client(AiProtocol.GOOGLE_GEMINI, transport=transport)
    with pytest.raises(LLMError) as excinfo:
        await client.fetch_models(BASE_URL, "gkey")
    assert excinfo.value.http_code == 400
    assert "API key not valid" in str(excinfo.value)
