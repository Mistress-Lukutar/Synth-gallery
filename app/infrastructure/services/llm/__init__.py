'''
File:   __init__.py
Brief:  Provider-agnostic LLM client package (chat streaming, tools, models).
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from app.infrastructure.services.llm.anthropic_client import AnthropicClient
from app.infrastructure.services.llm.factory import get_llm_client
from app.infrastructure.services.llm.gemini_client import GeminiClient
from app.infrastructure.services.llm.openai_client import OpenAiClient
from app.infrastructure.services.llm.sse import iter_sse_frames
from app.infrastructure.services.llm.types import (
    PROTOCOL_DEFAULT_BASE_URLS,
    AiProtocol,
    ChatRequest,
    Endpoint,
    ImagePart,
    LLMClient,
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
    ToolSpec,
    Turn,
    TurnComplete,
    guess_model_capabilities,
)

__all__ = [
    "AiProtocol",
    "PROTOCOL_DEFAULT_BASE_URLS",
    "LLMError",
    "LLMClient",
    "ImagePart",
    "TextPart",
    "ToolCall",
    "ToolResult",
    "Turn",
    "ToolSpec",
    "Endpoint",
    "ChatRequest",
    "StopReason",
    "ModelInfo",
    "TextDelta",
    "ReasoningDelta",
    "ToolCallsReady",
    "TurnComplete",
    "StreamError",
    "guess_model_capabilities",
    "get_llm_client",
    "OpenAiClient",
    "AnthropicClient",
    "GeminiClient",
    "iter_sse_frames",
]
