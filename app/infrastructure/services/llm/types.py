'''
File:   types.py
Brief:  Protocol-agnostic types for the LLM client package.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional, Protocol


class AiProtocol(str, Enum):
    '''Supported provider protocols.'''

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GOOGLE_GEMINI = "google_gemini"


PROTOCOL_DEFAULT_BASE_URLS: dict = {
    AiProtocol.OPENAI_COMPATIBLE.value: "https://api.openai.com/v1",
    AiProtocol.ANTHROPIC.value: "https://api.anthropic.com/v1",
    AiProtocol.GOOGLE_GEMINI.value: "https://generativelanguage.googleapis.com/v1beta",
}


class LLMError(Exception):
    '''Provider HTTP or protocol failure. http_code is set for HTTP errors.'''

    def __init__(self, message: str, http_code: Optional[int] = None):
        super().__init__(message)
        self.http_code = http_code


# ======================================================================
# Conversation parts / turns.
# ======================================================================

@dataclass
class ImagePart:
    '''Inline base64 image.'''

    mime_type: str
    data_b64: str

@dataclass
class TextPart:
    '''Plain text content.'''

    text: str

@dataclass
class ToolCall:
    '''Assistant request to invoke a tool.'''

    call_id: str
    name: str
    arguments_json: str   # raw JSON string as received

@dataclass
class ToolResult:
    '''Tool execution result fed back to the model.'''

    call_id: str
    name: str
    content: str
    images: list = field(default_factory=list)   # list[ImagePart]
    is_error: bool = False

@dataclass
class Turn:
    '''One conversation turn. role: "user" | "assistant" | "tool".'''

    role: str
    parts: list = field(default_factory=list)    # list[TextPart | ImagePart | ToolCall | ToolResult]

@dataclass
class ToolSpec:
    '''Tool advertised to the model.'''

    name: str
    description: str
    parameters_json_schema: dict

@dataclass
class Endpoint:
    '''Provider endpoint the client talks to.'''

    base_url: str
    api_key: str
    model_id: str
    protocol: AiProtocol

@dataclass
class ChatRequest:
    '''A full chat completion request.'''

    endpoint: Endpoint
    system_prompt: Optional[str]
    turns: list
    tools: list = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: Optional[int] = None   # None = omit; Anthropic falls back to 8192


class StopReason(str, Enum):
    '''Why the model stopped generating.'''

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    OTHER = "other"


@dataclass
class ModelInfo:
    '''Model advertised by a provider.'''

    model_id: str
    display_name: str
    supports_tools: bool = False
    supports_vision: bool = False
    context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


# ======================================================================
# Stream events yielded by LLMClient.stream_chat().
# ======================================================================

@dataclass
class TextDelta:
    '''Incremental assistant text.'''

    text: str

@dataclass
class ReasoningDelta:
    '''Incremental reasoning/thinking text (models that expose it).'''

    text: str

@dataclass
class ToolCallsReady:
    '''The model finished a turn requesting tool invocations.'''

    tool_calls: list      # list[ToolCall]
    text: str = ""        # assistant text preceding the tool calls

@dataclass
class TurnComplete:
    '''End of one model turn.'''

    stop_reason: StopReason
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

@dataclass
class StreamError:
    '''Non-fatal stream failure (HTTP or protocol).'''

    message: str
    http_code: Optional[int] = None


# ======================================================================
# Heuristics / client protocol.
# ======================================================================

# Model ids that clearly are not chat-completion models.
_NON_CHAT_KEYWORDS: tuple = (
    "embed", "whisper", "tts", "dall-e", "imagen", "moderation",
    "stable-diffusion", "flux", "rerank", "guard",
)

# Keywords that indicate vision-capable chat models.
_VISION_KEYWORDS: tuple = (
    "gpt-4o", "gpt-4.1", "gpt-5", "vision", "-vl", "claude-3", "claude-4",
    "gemini", "pixtral", "llava", "internvl", "glm-4v", "o4",
)


def guess_model_capabilities(model_id: str) -> tuple[bool, bool]:
    '''Heuristic (supports_tools, supports_vision) from the model id, case-insensitive.

    supports_tools: False only for clearly non-chat models (id contains any of:
    embed, whisper, tts, dall-e, imagen, moderation, stable-diffusion, flux,
    rerank, guard). Otherwise True.
    supports_vision: True when the id matches vision-capable keywords, e.g.:
    gpt-4o, gpt-4.1, gpt-5, vision, '-vl' (qwen-vl, qwen2-vl), claude-3, claude-4,
    gemini, pixtral, llava, internvl, glm-4v, o4. Otherwise False.
    '''
    lowered = model_id.lower()
    supports_tools = not any(kw in lowered for kw in _NON_CHAT_KEYWORDS)
    supports_vision = any(kw in lowered for kw in _VISION_KEYWORDS)
    return supports_tools, supports_vision


class LLMClient(Protocol):
    '''Provider-specific client contract.'''

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator:
        '''Yield TextDelta | ReasoningDelta | ToolCallsReady | TurnComplete | StreamError.'''
        ...
    async def fetch_models(self, base_url: str, api_key: str) -> list:
        '''Return list[ModelInfo]; raise LLMError on failure.'''
        ...
