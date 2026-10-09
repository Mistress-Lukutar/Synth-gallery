'''
File:   anthropic_client.py
Brief:  Anthropic Messages API client (SSE streaming, tool calling, models).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import httpx

from app.infrastructure.services.llm.sse import iter_sse_frames
from app.infrastructure.services.llm.types import (
    ChatRequest,
    ImagePart,
    LLMError,
    ModelInfo,
    ReasoningDelta,
    StopReason,
    StreamError,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallsReady,
    ToolResult,
    Turn,
    TurnComplete,
    guess_model_capabilities,
)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192

_MAX_ERROR_BODY = 300

_STOP_REASON_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.LENGTH,
    "refusal": StopReason.CONTENT_FILTER,
}


def _endpoint_url(base_url: str, path: str) -> str:
    '''Join a base URL and an endpoint path, tolerating a trailing slash.'''
    return base_url.rstrip("/") + path


def _error_message(body: str) -> str:
    '''Extract a human-readable error message from an error response body.

    Prefers the provider's ``error.message`` field; falls back to the raw
    body truncated to a sane length.
    '''
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body[:_MAX_ERROR_BODY]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    return body[:_MAX_ERROR_BODY]


def _parse_json_object(raw: str) -> dict:
    '''Parse a JSON object string, falling back to an empty object.'''
    try:
        parsed = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _image_block(part: ImagePart) -> dict:
    '''Map an inline image to an Anthropic content block.'''
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": part.mime_type,
            "data": part.data_b64,
        },
    }


def _user_blocks(parts: list) -> list[dict]:
    '''Map user-turn parts to Anthropic content blocks.'''
    blocks: list[dict] = []
    for part in parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            blocks.append(_image_block(part))
    return blocks


def _build_messages(turns: list[Turn]) -> list[dict]:
    '''Map protocol-agnostic turns to Anthropic ``messages``.

    Anthropic requires strictly alternating user/assistant roles, so every
    run of consecutive "tool" turns is folded into ONE user message holding
    one ``tool_result`` block per ToolResult (plus any image blocks the
    results carry).
    '''
    messages: list[dict] = []
    last_was_tool_fold = False
    for turn in turns:
        if turn.role == "user":
            blocks = _user_blocks(turn.parts)
            if blocks:
                messages.append({"role": "user", "content": blocks})
            last_was_tool_fold = False
        elif turn.role == "assistant":
            blocks: list[dict] = []
            for part in turn.parts:
                if isinstance(part, TextPart):
                    blocks.append({"type": "text", "text": part.text})
                elif isinstance(part, ToolCall):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": part.call_id,
                            "name": part.name,
                            "input": _parse_json_object(part.arguments_json),
                        }
                    )
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            last_was_tool_fold = False
        elif turn.role == "tool":
            blocks = []
            for part in turn.parts:
                if isinstance(part, ToolResult):
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": part.call_id,
                            "content": part.content,
                        }
                    )
                    for image in part.images:
                        blocks.append(_image_block(image))
            if blocks:
                if last_was_tool_fold:
                    # Fold into the previous user message (strict alternation).
                    messages[-1]["content"].extend(blocks)
                else:
                    messages.append({"role": "user", "content": blocks})
                last_was_tool_fold = True
    return messages


def _build_payload(request: ChatRequest) -> dict:
    '''Build the ``/messages`` request body.'''
    payload: dict = {
        "model": request.endpoint.model_id,
        # Anthropic has no default: max_tokens is mandatory.
        "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
        "stream": True,
        "temperature": request.temperature,
        "messages": _build_messages(request.turns),
    }
    if request.system_prompt:
        payload["system"] = request.system_prompt
    if request.tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters_json_schema,
            }
            for tool in request.tools
        ]
    return payload


class AnthropicClient:
    '''Client for the Anthropic Messages wire protocol.'''

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        '''Common request headers (auth + API version).'''
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Streaming chat.
    # ------------------------------------------------------------------

    async def stream_chat(
        self, request: ChatRequest
    ) -> AsyncIterator[TextDelta | ReasoningDelta | ToolCallsReady | TurnComplete | StreamError]:
        '''Stream one model turn, yielding typed events.

        Args:
            request: Full chat request (endpoint, turns, tools).

        Yields:
            TextDelta / ReasoningDelta as content arrives, ToolCallsReady
            when the model requested tools, TurnComplete when the turn
            finishes, or StreamError on HTTP/protocol failure.
        '''
        url = _endpoint_url(request.endpoint.base_url, "/messages")
        payload = _build_payload(request)

        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        stop_reason: Optional[StopReason] = None
        text_parts: list[str] = []
        # index -> {"type", "id", "name", "json": [partial strings]}
        open_blocks: dict[int, dict] = {}

        try:
            async with self._http.stream(
                "POST", url, headers=self._headers(request.endpoint.api_key),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    yield StreamError(_error_message(body), response.status_code)
                    return

                async for event_name, data in iter_sse_frames(response.aiter_bytes()):
                    try:
                        chunk = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    event_type = chunk.get("type") or event_name

                    if event_type == "error":
                        error = chunk.get("error") or {}
                        message = (
                            error.get("message", "Unknown provider error")
                            if isinstance(error, dict)
                            else str(error)
                        )
                        yield StreamError(str(message))
                        return
                    if event_type == "message_start":
                        message = chunk.get("message") or {}
                        usage = message.get("usage") or {}
                        input_tokens = usage.get("input_tokens", input_tokens)
                    elif event_type == "content_block_start":
                        index = chunk.get("index", 0)
                        block = chunk.get("content_block") or {}
                        open_blocks[index] = {
                            "type": block.get("type"),
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "json": [],
                        }
                    elif event_type == "content_block_delta":
                        index = chunk.get("index", 0)
                        delta = chunk.get("delta") or {}
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                text_parts.append(text)
                                yield TextDelta(text)
                        elif delta_type == "thinking_delta":
                            text = delta.get("thinking") or ""
                            if text:
                                yield ReasoningDelta(text)
                        elif delta_type == "input_json_delta":
                            entry = open_blocks.setdefault(
                                index,
                                {"type": "tool_use", "id": None, "name": None, "json": []},
                            )
                            entry["json"].append(delta.get("partial_json") or "")
                    elif event_type == "message_delta":
                        delta = chunk.get("delta") or {}
                        raw_reason = delta.get("stop_reason")
                        if raw_reason is not None:
                            stop_reason = _STOP_REASON_MAP.get(raw_reason, StopReason.OTHER)
                        usage = chunk.get("usage") or {}
                        output_tokens = usage.get("output_tokens", output_tokens)
                    elif event_type == "message_stop":
                        break
                    # "ping" / "content_block_stop" need no handling.
        except httpx.RequestError as exc:
            yield StreamError(f"Request failed: {exc}")
            return

        # Assemble tool calls from every tool_use block, in block order.
        tool_calls: list[ToolCall] = []
        for index in sorted(open_blocks):
            entry = open_blocks[index]
            if entry.get("type") != "tool_use":
                continue
            arguments_json = "".join(entry["json"]) or "{}"
            tool_calls.append(
                ToolCall(
                    call_id=entry.get("id") or f"call_{len(tool_calls) + 1}",
                    name=entry.get("name") or "",
                    arguments_json=arguments_json,
                )
            )

        if tool_calls:
            yield ToolCallsReady(tool_calls=tool_calls, text="".join(text_parts))
        yield TurnComplete(
            stop_reason=stop_reason or StopReason.OTHER,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # ------------------------------------------------------------------
    # Model listing.
    # ------------------------------------------------------------------

    async def fetch_models(self, base_url: str, api_key: str) -> list[ModelInfo]:
        '''List models advertised by Anthropic.

        Args:
            base_url: API base URL (e.g. ``https://api.anthropic.com/v1``).
            api_key: ``x-api-key`` credential.

        Returns:
            Parsed models with heuristic capabilities.

        Raises:
            LLMError: On HTTP or network failure.
        '''
        url = _endpoint_url(base_url, "/models")
        try:
            response = await self._http.get(url, headers=self._headers(api_key))
        except httpx.RequestError as exc:
            raise LLMError(f"Request failed: {exc}")
        if response.status_code >= 400:
            raise LLMError(
                _error_message(response.text), http_code=response.status_code
            )
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"Invalid JSON from {url}") from exc

        models: list[ModelInfo] = []
        for entry in body.get("data") or []:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id") or ""
            if not model_id:
                continue
            display_name = entry.get("display_name") or model_id
            supports_tools, supports_vision = guess_model_capabilities(model_id)
            models.append(
                ModelInfo(
                    model_id=model_id,
                    display_name=display_name,
                    supports_tools=supports_tools,
                    supports_vision=supports_vision,
                )
            )
        return models
