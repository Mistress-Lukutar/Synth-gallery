'''
File:   openai_client.py
Brief:  OpenAI-compatible chat client (SSE streaming, tool calling, models).
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

_MAX_ERROR_BODY = 300

_FINISH_REASON_MAP: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "length": StopReason.LENGTH,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "content_filter": StopReason.CONTENT_FILTER,
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


def _turns_to_messages(turns: list[Turn]) -> list[dict]:
    '''Map protocol-agnostic turns to OpenAI chat ``messages`` entries.'''
    messages: list[dict] = []
    for turn in turns:
        if turn.role == "user":
            parts: list[dict] = []
            for part in turn.parts:
                if isinstance(part, TextPart):
                    parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ImagePart):
                    url = f"data:{part.mime_type};base64,{part.data_b64}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": parts})
        elif turn.role == "assistant":
            text = "".join(p.text for p in turn.parts if isinstance(p, TextPart))
            message: dict = {"role": "assistant", "content": text if text else None}
            calls = [p for p in turn.parts if isinstance(p, ToolCall)]
            if calls:
                message["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments_json,
                        },
                    }
                    for call in calls
                ]
            messages.append(message)
        elif turn.role == "tool":
            # One message per ToolResult.
            for part in turn.parts:
                if isinstance(part, ToolResult):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.call_id,
                            "content": part.content,
                        }
                    )
    return messages


def _build_payload(request: ChatRequest) -> dict:
    '''Build the ``/chat/completions`` request body.'''
    messages: list[dict] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.extend(_turns_to_messages(request.turns))

    payload: dict = {
        "model": request.endpoint.model_id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": request.temperature,
    }
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_json_schema,
                },
            }
            for tool in request.tools
        ]
    return payload


def _build_tool_calls(
    fragments: dict[int, dict],
) -> list[ToolCall]:
    '''Assemble accumulated tool-call fragments (keyed by index) in order.'''
    calls: list[ToolCall] = []
    for index in sorted(fragments):
        entry = fragments[index]
        arguments = "".join(entry["arguments"])
        calls.append(
            ToolCall(
                call_id=entry["id"] or f"call_{index + 1}",
                name=entry["name"],
                arguments_json=arguments,
            )
        )
    return calls


class OpenAiClient:
    '''Client for the OpenAI ``/chat/completions`` wire protocol (and any
    compatible provider: OpenRouter, vLLM, LM Studio, ...).'''

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

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
            when the model requests tools, TurnComplete when the turn
            finishes, or StreamError on HTTP/protocol failure.
        '''
        url = _endpoint_url(request.endpoint.base_url, "/chat/completions")
        headers = {
            "Authorization": f"Bearer {request.endpoint.api_key}",
            "Content-Type": "application/json",
        }
        payload = _build_payload(request)

        finish_reason: Optional[str] = None
        text_parts: list[str] = []
        tool_fragments: dict[int, dict] = {}
        usage_input: Optional[int] = None
        usage_output: Optional[int] = None

        try:
            async with self._http.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    yield StreamError(_error_message(body), response.status_code)
                    return

                async for _event, data in iter_sse_frames(response.aiter_bytes()):
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(chunk, dict):
                        continue

                    # Mid-stream error payloads (some compatible servers).
                    error = chunk.get("error")
                    if error is not None and not chunk.get("choices"):
                        message = (
                            error.get("message")
                            if isinstance(error, dict) and error.get("message")
                            else json.dumps(error)
                        )
                        code = error.get("code") if isinstance(error, dict) else None
                        yield StreamError(str(message), code if isinstance(code, int) else None)
                        return

                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        # Usage typically arrives in a final chunk after the
                        # finish_reason chunk (include_usage), hence deferred
                        # TurnComplete below.
                        usage_input = usage.get("prompt_tokens", usage_input)
                        usage_output = usage.get("completion_tokens", usage_output)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        text_parts.append(content)
                        yield TextDelta(content)

                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        yield ReasoningDelta(reasoning)

                    for fragment in delta.get("tool_calls") or []:
                        index = fragment.get("index", 0)
                        entry = tool_fragments.setdefault(
                            index, {"id": "", "name": "", "arguments": []}
                        )
                        if fragment.get("id"):
                            entry["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            entry["name"] = function["name"]
                        if function.get("arguments"):
                            entry["arguments"].append(function["arguments"])

                    reason = choice.get("finish_reason")
                    if reason is not None:
                        finish_reason = reason
                        if reason in ("tool_calls", "function_call"):
                            yield ToolCallsReady(
                                tool_calls=_build_tool_calls(tool_fragments),
                                text="".join(text_parts),
                            )
        except httpx.RequestError as exc:
            yield StreamError(f"Request failed: {exc}")
            return

        if finish_reason is None:
            yield StreamError("Stream ended unexpectedly")
            return
        yield TurnComplete(
            stop_reason=_FINISH_REASON_MAP.get(finish_reason, StopReason.OTHER),
            input_tokens=usage_input,
            output_tokens=usage_output,
        )

    # ------------------------------------------------------------------
    # Model listing.
    # ------------------------------------------------------------------

    async def fetch_models(self, base_url: str, api_key: str) -> list[ModelInfo]:
        '''List models advertised by the provider.

        Args:
            base_url: API base URL (e.g. ``https://api.openai.com/v1``).
            api_key: Bearer token.

        Returns:
            Parsed models with heuristic capabilities.

        Raises:
            LLMError: On HTTP or network failure.
        '''
        url = _endpoint_url(base_url, "/models")
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await self._http.get(url, headers=headers)
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
            top_provider = entry.get("top_provider") or {}
            context_tokens = entry.get("context_length")
            if context_tokens is None:
                context_tokens = top_provider.get("context_length")
            max_output = entry.get("max_completion_tokens")
            if max_output is None:
                max_output = top_provider.get("max_completion_tokens")
            supports_tools, supports_vision = guess_model_capabilities(model_id)
            models.append(
                ModelInfo(
                    model_id=model_id,
                    display_name=model_id,
                    supports_tools=supports_tools,
                    supports_vision=supports_vision,
                    context_tokens=context_tokens,
                    max_output_tokens=max_output,
                )
            )
        return models
