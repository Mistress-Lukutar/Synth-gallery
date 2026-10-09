"""Built-in AI chat routes - conversations, streamed turns, vision decisions.

Turn events are delivered as Server-Sent Events. Event names/payloads:

- ``token``          {"text": str}
- ``reasoning``      {"text": str}
- ``tool_start``     {"call_id": str, "name": str, "arguments": object|string}
- ``tool_result``    {"call_id": str, "name": str, "summary": str, "is_error": bool}
- ``vision_request`` {"request_id": str, "items": [{"id": str, "title": str|None}]}
- ``turn_done``      {"stop_reason": str, "usage": {"input_tokens": int,
                      "output_tokens": int}|null}
- ``error``          {"message": str}
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..application.services.ai_chat_service import (
    AiChatService,
    ConversationNotFoundError,
    VisionRequestExpiredError,
    VisionRequestNotFoundError,
)
from ..application.services.ai_provider_service import AiProviderService
from ..database import create_connection
from ..dependencies import require_user
from ..infrastructure.repositories.ai_chat_repository import AiChatRepository
from ..infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)
from ..infrastructure.repositories.ai_model_repository import AiModelRepository
from ..infrastructure.repositories.ai_provider_repository import (
    AiProviderRepository,
)
from ..infrastructure.repositories.item_media_repository import (
    ItemMediaRepository,
)
from ..infrastructure.repositories.item_repository import ItemRepository
from ..infrastructure.services.encryption import dek_cache

router = APIRouter(prefix="/api/ai/chat", tags=["ai-chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


def build_ai_chat_service(db) -> AiChatService:
    """Create AiChatService with its repositories on one connection."""
    return AiChatService(
        chat_repo=AiChatRepository(db),
        item_repo=ItemRepository(db),
        item_media_repo=ItemMediaRepository(db),
        provider_service=AiProviderService(
            provider_repo=AiProviderRepository(db),
            model_repo=AiModelRepository(db),
            settings_repo=AiChatSettingsRepository(db),
        ),
    )


# ============================================================================
# Request bodies
# ============================================================================

class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    item_ids: Optional[List[str]] = None


class VisionDecisionRequest(BaseModel):
    approved: bool


# ============================================================================
# Helpers
# ============================================================================

def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Conversation not found")


def _precheck_turn(service: AiChatService, user: dict, conversation_id: str):
    """Shared pre-stream checks; returns the user's DEK.

    Raises:
        HTTPException: 404 unknown conversation, 409 busy, 403 missing DEK,
            400 provider not configured.
    """
    service.require_conversation(user["id"], conversation_id)
    if service.is_busy(user["id"]):
        raise HTTPException(
            status_code=409, detail="A chat turn is already running"
        )
    dek = dek_cache.get(user["id"])
    if dek is None:
        raise HTTPException(
            status_code=403, detail="Encryption key not available"
        )
    if service.provider_service.get_active_endpoint(user["id"], dek) is None:
        raise HTTPException(
            status_code=400, detail="AI provider is not configured"
        )
    return dek


def _sse_stream(build_generator):
    """Wrap an async ``(event, payload)`` generator into an SSE response.

    ``build_generator`` receives the AiChatService built on the stream's
    own connection and returns the event generator.
    """

    async def event_stream():
        db = create_connection()
        try:
            service = build_ai_chat_service(db)
            async for name, payload in build_generator(service):
                yield f"event: {name}\ndata: {json.dumps(payload)}\n\n"
        finally:
            db.close()

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


# ============================================================================
# Conversations
# ============================================================================

@router.post("/conversations")
def create_conversation(request: Request, data: Optional[CreateConversationRequest] = None):
    """Create a chat conversation."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        conversation = service.create_conversation(
            user["id"], (data.title if data else None) or "New chat"
        )
        return {"conversation": conversation}
    finally:
        db.close()


@router.get("/conversations")
def list_conversations(request: Request):
    """List the user's conversations, most recently updated first."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        return {"conversations": service.list_conversations(user["id"])}
    finally:
        db.close()


@router.delete("/conversations/{conversation_id}")
def delete_conversation(request: Request, conversation_id: str):
    """Delete a conversation and all its messages."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        service.delete_conversation(user["id"], conversation_id)
        return {"status": "ok"}
    except ConversationNotFoundError:
        raise _not_found()
    finally:
        db.close()


@router.get("/conversations/{conversation_id}/messages")
def get_messages(request: Request, conversation_id: str):
    """Get the stored messages of a conversation."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        return {"messages": service.get_messages(user["id"], conversation_id)}
    except ConversationNotFoundError:
        raise _not_found()
    finally:
        db.close()


# ============================================================================
# Streamed turns
# ============================================================================

@router.post("/conversations/{conversation_id}/messages")
def post_message(
    request: Request, conversation_id: str, data: SendMessageRequest
):
    """Send a message and stream the assistant's turn as SSE."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        dek = _precheck_turn(service, user, conversation_id)
    except ConversationNotFoundError:
        raise _not_found()
    finally:
        db.close()

    def build_generator(service):
        return service.run_message_turn(
            user["id"], dek, conversation_id, data.text, data.item_ids
        )

    return _sse_stream(build_generator)


@router.post("/conversations/{conversation_id}/vision/{request_id}/decision")
def vision_decision(
    request: Request, conversation_id: str, request_id: str,
    data: VisionDecisionRequest,
):
    """Approve or deny a vision request; streams the resumed turn."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        service.require_conversation(user["id"], conversation_id)
        service.check_vision_request(user["id"], conversation_id, request_id)
        if service.is_busy(user["id"]):
            raise HTTPException(
                status_code=409, detail="A chat turn is already running"
            )
        dek = dek_cache.get(user["id"])
        if dek is None:
            raise HTTPException(
                status_code=403, detail="Encryption key not available"
            )
        if service.provider_service.get_active_endpoint(user["id"], dek) is None:
            raise HTTPException(
                status_code=400, detail="AI provider is not configured"
            )
    except ConversationNotFoundError:
        raise _not_found()
    except VisionRequestNotFoundError:
        raise HTTPException(
            status_code=404, detail="Vision request not found"
        )
    except VisionRequestExpiredError:
        raise HTTPException(
            status_code=410, detail="Vision request expired"
        )
    finally:
        db.close()

    def build_generator(service):
        return service.resume_vision(
            user["id"], dek, conversation_id, request_id, data.approved
        )

    return _sse_stream(build_generator)
