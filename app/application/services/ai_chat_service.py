"""AI chat orchestrator - turns the stored chat into LLM calls and back.

The service owns the tool-execution loop: it replays stored messages as
provider-agnostic LLM turns, streams the model's answer to the route as
``(event_name, payload)`` tuples, executes requested tools via the
:mod:`~app.application.services.ai_tools` registry and persists every
step. ``view_images`` pauses the turn until the user approves.

LLM access goes through the module-level :func:`_get_llm_client` and
nowhere else, so tests can substitute a fake client at a single point.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import AsyncIterator, Dict, List, Optional, Set, Tuple

from PIL import Image

from ...infrastructure.services.encryption import EncryptionService
from ...infrastructure.services.llm.types import (
    AiProtocol,
    ChatRequest,
    Endpoint,
    ImagePart,
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
)
from ...infrastructure.storage import get_storage
from ...logging_config import get_logger
from .ai_tools import (
    TOOL_REGISTRY,
    TOOL_SPECS,
    ToolContext,
    ToolError,
    VisionRequestSignal,
)

logger = get_logger(__name__)


# ============================================================================
# Errors the routes map to HTTP status codes.
# ============================================================================

class ConversationNotFoundError(Exception):
    """Conversation does not exist or is not owned by the user (404)."""


class BusyError(Exception):
    """The user already has a chat turn running (409)."""


class VisionRequestNotFoundError(Exception):
    """Vision request does not exist or belongs elsewhere (404)."""


class VisionRequestExpiredError(Exception):
    """Vision request timed out (410)."""


# ============================================================================
# LLM client access - the single point tests monkeypatch.
# ============================================================================

def _get_llm_client(protocol: AiProtocol):
    """Return the LLM client for ``protocol`` (lazily imported)."""
    from app.infrastructure.services.llm import get_llm_client

    return get_llm_client(protocol)


# ============================================================================
# In-memory state (single-process app).
# ============================================================================

@dataclass
class VisionRequest:
    """A paused ``view_images`` call waiting for the user's decision."""

    request_id: str
    user_id: int
    conversation_id: str
    tool_call_id: str
    item_ids: List[str]
    created_at: float


_active_users: Set[int] = set()
_active_users_lock = threading.Lock()
_pending_vision: Dict[str, VisionRequest] = {}
# (conversation_id, tool_call_id) -> {item_id: (mime_type, base64)}
_turn_images: Dict[Tuple[str, str], Dict[str, tuple]] = {}


class AiChatService:
    """Orchestrates one chat turn: LLM streaming + tool execution."""

    MAX_TOOL_ITERATIONS = 25
    TOOL_RESULT_LIMIT = 8000
    VISION_MAX_ITEMS = 8
    VISION_TTL_SECONDS = 1800

    def __init__(self, chat_repo, item_repo, item_media_repo, provider_service):
        """Create the service with its repositories.

        Args:
            chat_repo: AiChatRepository instance.
            item_repo: ItemRepository instance.
            item_media_repo: ItemMediaRepository instance.
            provider_service: AiProviderService instance.
        """
        self.chat_repo = chat_repo
        self.item_repo = item_repo
        self.item_media_repo = item_media_repo
        self.provider_service = provider_service

    # =====================================================================
    # User-slot state
    # =====================================================================

    def is_busy(self, user_id: int) -> bool:
        """True when the user already has a chat turn running."""
        return user_id in _active_users

    @staticmethod
    def _acquire_slot(user_id: int) -> None:
        with _active_users_lock:
            if user_id in _active_users:
                raise BusyError(f"user {user_id} already has a running turn")
            _active_users.add(user_id)

    @staticmethod
    def _release_slot(user_id: int) -> None:
        with _active_users_lock:
            _active_users.discard(user_id)

    # =====================================================================
    # Pending vision requests
    # =====================================================================

    @classmethod
    def _purge_expired_vision(cls) -> None:
        now = time.time()
        expired = [
            request_id
            for request_id, request in _pending_vision.items()
            if now - request.created_at > cls.VISION_TTL_SECONDS
        ]
        for request_id in expired:
            _pending_vision.pop(request_id, None)

    @classmethod
    def get_pending(cls, conversation_id: str) -> Optional[VisionRequest]:
        """Return the active pending vision request for a conversation."""
        cls._purge_expired_vision()
        for request in _pending_vision.values():
            if request.conversation_id == conversation_id:
                return request
        return None

    @staticmethod
    def _clear_turn_images(conversation_id: str) -> None:
        for key in [key for key in _turn_images if key[0] == conversation_id]:
            _turn_images.pop(key, None)

    def check_vision_request(
        self, user_id: int, conversation_id: str, request_id: str
    ) -> VisionRequest:
        """Validate a vision request before streaming (404/410 pre-checks).

        Raises:
            VisionRequestNotFoundError: Unknown request or wrong owner/
                conversation.
            VisionRequestExpiredError: The request timed out.
        """
        # No sweep here: an expired-but-unresolved request must stay
        # reachable so the decision endpoint can answer 410 (it is popped
        # in the expired branch below); get_pending() purges for everyone
        # else.
        request = _pending_vision.get(request_id)
        if (
            request is None
            or request.user_id != user_id
            or request.conversation_id != conversation_id
        ):
            raise VisionRequestNotFoundError(
                f"vision request {request_id} not found"
            )
        if time.time() - request.created_at > self.VISION_TTL_SECONDS:
            _pending_vision.pop(request_id, None)
            raise VisionRequestExpiredError("vision request expired")
        return request

    # =====================================================================
    # Ownership-checked thin wrappers
    # =====================================================================

    def require_conversation(self, user_id: int, conversation_id: str) -> dict:
        """Return the conversation or raise :class:`ConversationNotFoundError`."""
        conversation = self.chat_repo.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError(
                f"conversation {conversation_id} not found"
            )
        return conversation

    def list_conversations(self, user_id: int) -> List[dict]:
        return self.chat_repo.list_conversations(user_id)

    def create_conversation(self, user_id: int, title: str = "New chat") -> dict:
        return self.chat_repo.create_conversation(user_id, title)

    def delete_conversation(self, user_id: int, conversation_id: str) -> bool:
        if not self.chat_repo.delete_conversation(conversation_id, user_id):
            raise ConversationNotFoundError(
                f"conversation {conversation_id} not found"
            )
        return True

    def get_messages(
        self, user_id: int, conversation_id: str, limit: int = 100
    ) -> List[dict]:
        self.require_conversation(user_id, conversation_id)
        return self.chat_repo.get_messages(conversation_id, limit)

    # =====================================================================
    # System prompt
    # =====================================================================

    @staticmethod
    def _build_system_prompt() -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            "You are the AI assistant of Synth Gallery, a personal "
            "encrypted media vault that stores the owner's photos, videos "
            "and text notes, organized into folders, albums and tags.\n"
            f"Today's date: {today}.\n"
            "Rules:\n"
            "- Act only through the provided tools; never invent tool "
            "results.\n"
            "- All queries are automatically scoped to the owner of the "
            "library.\n"
            "- view_images requires the user's approval; never claim to "
            "see image content you have not actually been shown.\n"
            "- If a vision request is denied or expired, you have no "
            "visual access: say so and continue without it.\n"
            "- Tags must exist before use: check with list_tags / "
            "get_tag_info and create missing ones with create_tags. "
            "Implications resolve automatically - never add implied tags "
            "yourself.\n"
            "- When tagging images in bulk, work in batches of at most 8 "
            "images per view_images call.\n"
            "- Never invent item ids; obtain them from tools first.\n"
            "- Reply in the user's language. Be concise."
        )

    # =====================================================================
    # Turn entry points
    # =====================================================================

    async def run_message_turn(
        self,
        user_id: int,
        dek: bytes,
        conversation_id: str,
        text: str,
        attached_item_ids: Optional[List[str]] = None,
    ) -> AsyncIterator:
        """Run one user-message turn, yielding ``(event, payload)`` tuples.

        Raises:
            ConversationNotFoundError: Unknown or foreign conversation.
            BusyError: The user already has a running turn.
        """
        self.require_conversation(user_id, conversation_id)
        self._acquire_slot(user_id)
        try:
            parts = [{"type": "text", "text": text}]
            if attached_item_ids:
                parts.append({
                    "type": "context",
                    "item_ids": [str(item_id) for item_id in attached_item_ids],
                })
            self.chat_repo.add_message(conversation_id, "user", parts)

            user_message_count = sum(
                1
                for message in self.chat_repo.get_messages(conversation_id)
                if message["role"] == "user"
            )
            if user_message_count == 1:
                self.chat_repo.set_title(conversation_id, user_id, text[:60])

            # A new user message invalidates hydrated images of past turns.
            self._clear_turn_images(conversation_id)

            inner = self._run_llm_loop(user_id, dek, conversation_id)
            try:
                async for event in inner:
                    yield event
            finally:
                # Deterministically close the inner generator so its
                # finally-block (partial-text persistence) runs while the
                # caller's connection is still open.
                await inner.aclose()
        finally:
            self._release_slot(user_id)
            self._clear_turn_images(conversation_id)

    async def resume_vision(
        self,
        user_id: int,
        dek: bytes,
        conversation_id: str,
        request_id: str,
        approved: bool,
    ) -> AsyncIterator:
        """Resolve a vision request and continue the paused turn.

        Raises:
            ConversationNotFoundError: Unknown or foreign conversation.
            VisionRequestNotFoundError / VisionRequestExpiredError.
            BusyError: The user already has a running turn.
        """
        self.require_conversation(user_id, conversation_id)
        request = self.check_vision_request(user_id, conversation_id, request_id)
        _pending_vision.pop(request_id, None)
        self._acquire_slot(user_id)
        try:
            tool_call_id = request.tool_call_id
            if approved:
                images: Dict[str, tuple] = {}
                failed: List[str] = []
                for item_id in request.item_ids:
                    loaded = await self._load_item_image(item_id, dek)
                    if loaded is None:
                        failed.append(item_id)
                    else:
                        images[item_id] = loaded
                if images:
                    _turn_images[(conversation_id, tool_call_id)] = images
                content = f"User approved. {len(images)} image(s) attached."
                if failed:
                    content += " Could not load: " + ", ".join(failed) + "."
                parts = [{
                    "type": "tool_result",
                    "call_id": tool_call_id,
                    "name": "view_images",
                    "content": content,
                    "images": [
                        item_id for item_id in request.item_ids
                        if item_id in images
                    ],
                    "is_error": False,
                }]
            else:
                content = (
                    "User denied viewing these images. Continue without "
                    "visual access."
                )
                parts = [{
                    "type": "tool_result",
                    "call_id": tool_call_id,
                    "name": "view_images",
                    "content": content,
                    "images": [],
                    "is_error": False,
                }]
            self.chat_repo.add_message(conversation_id, "tool", parts)

            inner = self._run_llm_loop(user_id, dek, conversation_id)
            try:
                async for event in inner:
                    yield event
            finally:
                await inner.aclose()
        finally:
            self._release_slot(user_id)
            self._clear_turn_images(conversation_id)

    # =====================================================================
    # Vision image loading
    # =====================================================================

    async def _load_item_image(
        self, item_id: str, dek: bytes
    ) -> Optional[Tuple[str, str]]:
        """Load, decrypt and downscale one image for the LLM.

        Returns ``(mime_type, base64)`` or None when the image cannot be
        loaded (missing file, decode failure, ...).
        """
        media = self.item_media_repo.get_by_item_id(item_id)
        if media is None:
            return None

        content_type = (media.get("content_type") or "").lower()
        if media.get("media_type") == "video":
            folder = "thumbnails"  # never ship a whole video to the model
        elif content_type == "image/jxl":
            folder = "thumbnails"  # Pillow cannot decode JXL
        elif max(media.get("thumb_width") or 0, media.get("thumb_height") or 0) >= 512:
            folder = "thumbnails"
        else:
            folder = "uploads"

        try:
            storage = get_storage()
            blob = await storage.download(item_id, folder=folder)
            plain = EncryptionService.decrypt_bytes(blob, dek)
            with Image.open(BytesIO(plain)) as image:
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                if max(image.size) > 1024:
                    image.thumbnail((1024, 1024))
                out = BytesIO()
                image.save(out, "JPEG", quality=85)
            return ("image/jpeg", base64.b64encode(out.getvalue()).decode("ascii"))
        except Exception:
            logger.warning(
                "vision: could not load image for item %s", item_id, exc_info=True
            )
            return None

    # =====================================================================
    # The LLM loop (shared by both entry points)
    # =====================================================================

    async def _run_llm_loop(
        self, user_id: int, dek: bytes, conversation_id: str
    ) -> AsyncIterator:
        """Stream from the LLM and execute tools until the turn ends.

        Yields ``(event_name, payload)`` tuples. Persists assistant text
        incrementally: on stream errors, on normal completion and (as a
        partial answer) when the generator is closed mid-stream.
        """
        accumulated = ""
        text_persisted = True
        usage_input = 0
        usage_output = 0
        usage_reported = False
        interrupted = False
        try:
            for _iteration in range(self.MAX_TOOL_ITERATIONS):
                endpoint = self.provider_service.get_active_endpoint(user_id, dek)
                if endpoint is None:
                    yield ("error", {"message": "AI provider is not configured"})
                    return
                try:
                    protocol = AiProtocol(endpoint["protocol"])
                except (KeyError, ValueError):
                    yield (
                        "error",
                        {"message": f"unknown protocol: {endpoint.get('protocol')}"},
                    )
                    return

                request = ChatRequest(
                    endpoint=Endpoint(
                        base_url=endpoint["base_url"],
                        api_key=endpoint["api_key"],
                        model_id=endpoint["model_id"],
                        protocol=protocol,
                    ),
                    system_prompt=self._build_system_prompt(),
                    turns=self._build_llm_turns(conversation_id),
                    tools=list(TOOL_SPECS),
                    temperature=(
                        endpoint.get("temperature")
                        if endpoint.get("temperature") is not None
                        else 0.7
                    ),
                    max_tokens=None,
                )

                accumulated = ""
                text_persisted = True
                stream_error: Optional[StreamError] = None
                calls_ready: Optional[ToolCallsReady] = None
                stop: Optional[TurnComplete] = None

                client = _get_llm_client(protocol)
                stream = client.stream_chat(request)
                try:
                    async for event in stream:
                        if isinstance(event, TextDelta):
                            accumulated += event.text
                            text_persisted = False
                            yield ("token", {"text": event.text})
                        elif isinstance(event, ReasoningDelta):
                            yield ("reasoning", {"text": event.text})
                        elif isinstance(event, ToolCallsReady):
                            calls_ready = event
                        elif isinstance(event, TurnComplete):
                            stop = event
                            if (
                                event.input_tokens is not None
                                or event.output_tokens is not None
                            ):
                                usage_reported = True
                                usage_input += event.input_tokens or 0
                                usage_output += event.output_tokens or 0
                        elif isinstance(event, StreamError):
                            stream_error = event
                finally:
                    # Close provider streams deterministically (httpx
                    # responses must not linger until GC).
                    await stream.aclose()

                if stream_error is not None:
                    yield ("error", {"message": stream_error.message})
                    if accumulated:
                        self.chat_repo.add_message(
                            conversation_id,
                            "assistant",
                            [{"type": "text", "text": accumulated}],
                        )
                        text_persisted = True
                    return

                if calls_ready is not None:
                    self._persist_assistant_tool_calls(conversation_id, calls_ready)
                    text_persisted = True
                    paused = False
                    async for event in self._execute_tool_calls(
                        user_id, conversation_id, calls_ready
                    ):
                        if event[0] == "vision_request":
                            paused = True
                        yield event
                    if paused:
                        # The generator ends here; the turn resumes through
                        # resume_vision() after the user's decision.
                        return
                    continue

                if stop is not None and stop.stop_reason == StopReason.TOOL_USE:
                    # TOOL_USE without tool calls: bail out instead of looping.
                    if accumulated:
                        self.chat_repo.add_message(
                            conversation_id,
                            "assistant",
                            [{"type": "text", "text": accumulated}],
                        )
                        text_persisted = True
                    yield (
                        "error",
                        {"message": "model requested tools but provided no tool calls"},
                    )
                    return

                if accumulated:
                    self.chat_repo.add_message(
                        conversation_id,
                        "assistant",
                        [{"type": "text", "text": accumulated}],
                    )
                    text_persisted = True
                usage = (
                    {"input_tokens": usage_input, "output_tokens": usage_output}
                    if usage_reported
                    else None
                )
                yield (
                    "turn_done",
                    {
                        "stop_reason": (
                            stop.stop_reason.value if stop else StopReason.OTHER.value
                        ),
                        "usage": usage,
                    },
                )
                return

            # Iteration cap exhausted.
            yield ("error", {"message": "tool iteration limit reached"})
            self.chat_repo.add_message(
                conversation_id,
                "assistant",
                [{"type": "text", "text": "(stopped after tool iteration limit)"}],
            )
            text_persisted = True
        except (GeneratorExit, asyncio.CancelledError):
            interrupted = True
            raise
        finally:
            if interrupted and not text_persisted and accumulated:
                # Persist the partial answer before releasing the user slot.
                try:
                    self.chat_repo.add_message(
                        conversation_id,
                        "assistant",
                        [{"type": "text", "text": accumulated}],
                    )
                except Exception:
                    logger.exception(
                        "failed to persist partial assistant message for %s",
                        conversation_id,
                    )

    # =====================================================================
    # Tool execution
    # =====================================================================

    @staticmethod
    def _parse_arguments(raw) -> object:
        """Parse a tool-call arguments JSON string; return the raw value on
        failure."""
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return raw
        return raw

    def _persist_assistant_tool_calls(
        self, conversation_id: str, calls_ready
    ) -> None:
        """Store the assistant message carrying text and tool calls."""
        parts = []
        if calls_ready.text:
            parts.append({"type": "text", "text": calls_ready.text})
        for call in calls_ready.tool_calls:
            parsed = self._parse_arguments(call.arguments_json)
            parts.append({
                "type": "tool_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": parsed if isinstance(parsed, dict) else call.arguments_json,
            })
        self.chat_repo.add_message(conversation_id, "assistant", parts)

    async def _execute_tool_calls(
        self, user_id: int, conversation_id: str, calls_ready
    ):
        """Execute the model's tool calls, persisting each result.

        Yields ``(event_name, payload)`` tuples. Ends immediately after a
        ``vision_request`` event when a ``view_images`` call pauses the
        turn (the caller must then stop the generator).
        """
        for call in calls_ready.tool_calls:
            parsed = self._parse_arguments(call.arguments_json)
            yield ("tool_start", {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": (
                    parsed if isinstance(parsed, dict) else call.arguments_json
                ),
            })

            tool = TOOL_REGISTRY.get(call.name)
            if tool is None:
                content = f"unknown tool: {call.name}"
                is_error = True
            elif not isinstance(parsed, dict):
                content = "invalid arguments JSON"
                is_error = True
            else:
                try:
                    content = tool.executor(parsed, ToolContext(user_id=user_id))
                    is_error = False
                except VisionRequestSignal as signal:
                    request_id = uuid.uuid4().hex
                    _pending_vision[request_id] = VisionRequest(
                        request_id=request_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        tool_call_id=call.call_id,
                        item_ids=list(signal.item_ids),
                        created_at=time.time(),
                    )
                    yield ("vision_request", {
                        "request_id": request_id,
                        "items": self._item_titles(signal.item_ids),
                    })
                    return
                except ToolError as exc:
                    content = str(exc)
                    is_error = True
                except Exception:
                    logger.exception(
                        "tool %s failed unexpectedly", call.name
                    )
                    content = f"internal error while running tool {call.name}"
                    is_error = True

            content = self._truncate(content)
            self.chat_repo.add_message(conversation_id, "tool", [{
                "type": "tool_result",
                "call_id": call.call_id,
                "name": call.name,
                "content": content,
                "images": [],
                "is_error": is_error,
            }])
            yield ("tool_result", {
                "call_id": call.call_id,
                "name": call.name,
                "summary": content[:200],
                "is_error": is_error,
            })
        return

    @staticmethod
    def _truncate(content: str) -> str:
        if len(content) > AiChatService.TOOL_RESULT_LIMIT:
            return content[: AiChatService.TOOL_RESULT_LIMIT] + "…truncated"
        return content

    def _item_titles(self, item_ids: List[str]) -> List[dict]:
        """Resolve item titles for a vision_request payload."""
        items = []
        for item_id in item_ids:
            item = self.item_repo.get_by_id(item_id)
            items.append({
                "id": item_id,
                "title": item.get("title") if item else None,
            })
        return items

    # =====================================================================
    # History replay
    # =====================================================================

    def _build_llm_turns(self, conversation_id: str) -> List[Turn]:
        """Convert stored messages into provider-agnostic LLM turns."""
        messages = self.chat_repo.get_messages(conversation_id)
        return self._turns_from_messages(
            messages, conversation_id, self.get_pending(conversation_id)
        )

    @staticmethod
    def _turns_from_messages(
        messages: List[dict],
        conversation_id: str,
        pending_vision: Optional[VisionRequest] = None,
    ) -> List[Turn]:
        answered_calls = set()
        for message in messages:
            if message["role"] != "tool":
                continue
            for part in message["parts"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    answered_calls.add(part.get("call_id"))

        pending_call_id = (
            pending_vision.tool_call_id if pending_vision is not None else None
        )

        turns: List[Turn] = []
        for message in messages:
            role = message["role"]
            if role == "user":
                parts = []
                for part in message["parts"]:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        parts.append(TextPart(part.get("text", "")))
                    elif part.get("type") == "context":
                        ids = ", ".join(
                            str(item_id) for item_id in part.get("item_ids", [])
                        )
                        parts.append(TextPart(
                            "The user attached these library items: "
                            f"{ids} (use get_item / view_images to inspect them)"
                        ))
                if parts:
                    turns.append(Turn("user", parts))

            elif role == "assistant":
                kept = []
                synthetic: List[ToolResult] = []
                for part in message["parts"]:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and part.get("text"):
                        kept.append(TextPart(part["text"]))
                    elif part.get("type") == "tool_call":
                        raw = part.get("arguments")
                        if not isinstance(raw, str):
                            raw = json.dumps(raw or {}, ensure_ascii=False)
                        call = ToolCall(
                            call_id=part.get("call_id"),
                            name=part.get("name"),
                            arguments_json=raw,
                        )
                        if call.call_id not in answered_calls:
                            if call.call_id == pending_call_id:
                                # The pending vision request resolves this
                                # call on resume; dropping it keeps the
                                # replay valid in the meantime.
                                continue
                            synthetic.append(ToolResult(
                                call_id=call.call_id,
                                name=call.name,
                                content="(request expired — no response)",
                                is_error=True,
                            ))
                            continue
                        kept.append(call)
                if kept:
                    turns.append(Turn("assistant", kept))
                if synthetic:
                    turns.append(Turn("tool", synthetic))

            elif role == "tool":
                parts = []
                for part in message["parts"]:
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    image_ids = part.get("images") or []
                    buffer = _turn_images.get(
                        (conversation_id, part.get("call_id")), {}
                    )
                    image_parts = []
                    for item_id in image_ids:
                        entry = buffer.get(item_id)
                        if entry is not None:
                            image_parts.append(
                                ImagePart(mime_type=entry[0], data_b64=entry[1])
                            )
                    if image_ids:
                        hydrated = {img for img in image_ids if buffer.get(img)}
                        missing = [i for i in image_ids if i not in hydrated]
                        if missing:
                            parts.append(TextPart(
                                "(images of items "
                                f"{', '.join(missing)} were viewed earlier, "
                                "no longer available)"
                            ))
                    parts.append(ToolResult(
                        call_id=part.get("call_id"),
                        name=part.get("name"),
                        content=part.get("content", ""),
                        images=image_parts,
                        is_error=bool(part.get("is_error")),
                    ))
                if parts:
                    turns.append(Turn("tool", parts))

        return turns
