'''
File:   gemini_client.py
Brief:  Google Gemini client (SSE streaming, function calling, models).
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
    "STOP": StopReason.END_TURN,
    "MAX_TOKENS": StopReason.LENGTH,
    "SAFETY": StopReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": StopReason.CONTENT_FILTER,
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


def _build_contents(turns: list[Turn]) -> list[dict]:
    '''Map protocol-agnostic turns to Gemini ``contents``.

    Gemini requires alternating ``user`` / ``model`` roles, so consecutive
    turns mapping to the same role (e.g. two "tool" turns after one
    assistant tool-call turn) are merged into a single content entry.
    '''
    contents: list[dict] = []

    def _append(role: str, parts: list[dict]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for turn in turns:
        if turn.role == "user":
            parts: list[dict] = []
            for part in turn.parts:
                if isinstance(part, TextPart):
                    parts.append({"text": part.text})
                elif isinstance(part, ImagePart):
                    parts.append(
                        {
                            "inlineData": {
                                "mimeType": part.mime_type,
                                "data": part.data_b64,
                            }
                        }
                    )
            _append("user", parts)
        elif turn.role == "assistant":
            parts = []
            for part in turn.parts:
                if isinstance(part, TextPart):
                    parts.append({"text": part.text})
                elif isinstance(part, ToolCall):
                    parts.append(
                        {
                            "functionCall": {
                                "name": part.name,
                                "args": _parse_json_object(part.arguments_json),
                            }
                        }
                    )
            _append("model", parts)
        elif turn.role == "tool":
            parts = []
            for part in turn.parts:
                if isinstance(part, ToolResult):
                    parts.append(
                        {
                            "functionResponse": {
                                "name": part.name,
                                "response": {"result": part.content},
                            }
                        }
                    )
                    for image in part.images:
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": image.mime_type,
                                    "data": image.data_b64,
                                }
                            }
                        )
            _append("user", parts)
    return contents


def _build_payload(request: ChatRequest) -> dict:
    '''Build the ``streamGenerateContent`` request body.'''
    payload: dict = {"contents": _build_contents(request.turns)}
    if request.system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
    if request.tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_json_schema,
                    }
                    for tool in request.tools
                ]
            }
        ]
    generation_config: dict = {"temperature": request.temperature}
    if request.max_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_tokens
    payload["generationConfig"] = generation_config
    return payload


class GeminiClient:
    '''Client for the Google Gemini ``generateContent`` wire protocol.'''

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
            TextDelta as content arrives, ToolCallsReady when the model
            requested function calls, TurnComplete when the turn finishes,
            or StreamError on HTTP/protocol failure.
        '''
        url = (
            _endpoint_url(request.endpoint.base_url, "/models")
            + f"/{request.endpoint.model_id}:streamGenerateContent?alt=sse"
        )
        headers = {
            "x-goog-api-key": request.endpoint.api_key,
            "Content-Type": "application/json",
        }
        payload = _build_payload(request)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason: Optional[StopReason] = None
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None

        try:
            async with self._http.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    yield StreamError(_error_message(body), response.status_code)
                    return

                async for _event, data in iter_sse_frames(response.aiter_bytes()):
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(chunk, dict):
                        continue

                    # Error payloads arrive as {"error": {...}} frames.
                    error = chunk.get("error")
                    if isinstance(error, dict):
                        message = error.get("message") or json.dumps(error)
                        code = error.get("code")
                        yield StreamError(
                            str(message), code if isinstance(code, int) else None
                        )
                        return

                    usage = chunk.get("usageMetadata") or {}
                    input_tokens = usage.get("promptTokenCount", input_tokens)
                    output_tokens = usage.get("candidatesTokenCount", output_tokens)

                    candidates = chunk.get("candidates") or []
                    if not candidates:
                        continue
                    candidate = candidates[0]
                    raw_reason = candidate.get("finishReason")
                    if raw_reason is not None:
                        stop_reason = _FINISH_REASON_MAP.get(raw_reason, StopReason.OTHER)

                    content = candidate.get("content") or {}
                    for part in content.get("parts") or []:
                        if not isinstance(part, dict):
                            continue
                        text = part.get("text")
                        if text:
                            text_parts.append(text)
                            yield TextDelta(text)
                        function_call = part.get("functionCall")
                        if isinstance(function_call, dict):
                            # Gemini tool calls carry no ids — synthesize.
                            tool_calls.append(
                                ToolCall(
                                    call_id=f"call_{len(tool_calls) + 1}",
                                    name=function_call.get("name") or "",
                                    arguments_json=json.dumps(
                                        function_call.get("args") or {}
                                    ),
                                )
                            )
        except httpx.RequestError as exc:
            yield StreamError(f"Request failed: {exc}")
            return

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
        '''List models advertised by Google.

        Only entries supporting ``generateContent`` are returned.

        Args:
            base_url: API base URL (``.../v1beta``).
            api_key: ``x-goog-api-key`` credential.

        Returns:
            Parsed models with heuristic capabilities.

        Raises:
            LLMError: On HTTP or network failure.
        '''
        url = _endpoint_url(base_url, "/models")
        headers = {"x-goog-api-key": api_key}
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
        for entry in body.get("models") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or ""
            model_id = name.rsplit("/", 1)[-1]  # strip leading "models/"
            if not model_id:
                continue
            methods = entry.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            display_name = entry.get("displayName") or model_id
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
