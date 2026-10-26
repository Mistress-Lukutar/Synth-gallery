"""Built-in AI chat routes - conversations, streamed turns, vision decisions.

Turn events are delivered as Server-Sent Events. Event names/payloads:

- ``token``          {"text": str}
- ``reasoning``      {"text": str}
- ``tool_start``     {"call_id": str, "name": str, "arguments": object|string}
- ``tool_result``    {"call_id": str, "name": str, "summary": str,
                      "content": str, "is_error": bool}
- ``vision_request`` {"request_id": str, "items": [{"id": str, "title": str|None}]}
- ``tool_confirm``   {"request_id": str, "call_id": str, "name": str,
                      "arguments": object}
- ``turn_done``      {"stop_reason": str, "usage": {"input_tokens": int,
                      "output_tokens": int}|null}
- ``error``          {"message": str}
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..application.services.ai_agent_service import AgentNotFoundError
from ..application.services.ai_chat_service import (
    AiChatService,
    ConversationNotFoundError,
    MessageNotFoundError,
    ToolConfirmationExpiredError,
    ToolConfirmationNotFoundError,
    VisionRequestExpiredError,
    VisionRequestNotFoundError,
)
from ..application.services.album_service import AlbumService
from ..application.services.ai_provider_service import AiProviderService
from ..database import create_connection
from ..dependencies import require_user
from ..infrastructure.repositories.ai_agent_repository import AiAgentRepository
from ..infrastructure.repositories.ai_chat_repository import AiChatRepository
from ..infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)
from ..infrastructure.repositories.ai_model_repository import AiModelRepository
from ..infrastructure.repositories.ai_provider_repository import (
    AiProviderRepository,
)
from ..infrastructure.repositories.album_repository import AlbumRepository
from ..infrastructure.repositories.folder_repository import FolderRepository
from ..infrastructure.repositories.item_media_repository import (
    ItemMediaRepository,
)
from ..infrastructure.repositories.item_repository import ItemRepository
from ..infrastructure.repositories.permission_repository import PermissionRepository
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
        agent_repo=AiAgentRepository(db),
    )


# ============================================================================
# Request bodies
# ============================================================================

class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    agent_id: Optional[int] = None


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    item_ids: Optional[List[str]] = None
    # Attached albums are expanded to their member items before the turn.
    album_ids: Optional[List[str]] = None
    # Edit/retry: the user message this turn replaces with a new version.
    # The new message branches off the original's parent and becomes the
    # active branch; the old branch stays in the tree.
    edit_of_message_id: Optional[int] = None


class BranchSwitchRequest(BaseModel):
    message_id: int


class VisionDecisionRequest(BaseModel):
    approved: bool


class ToolDecisionRequest(BaseModel):
    approved: bool
    # Remember the approval for this tool in this chat session.
    always_allow: bool = False


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
    """Create a chat conversation, optionally bound to an owned agent."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        try:
            conversation = service.create_conversation(
                user["id"],
                (data.title if data else None) or "New chat",
                agent_id=(data.agent_id if data else None),
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
    """Get the stored messages of a conversation.

    The response also carries ``pending_vision`` / ``pending_tool_confirm``
    when the conversation's turn is paused on an approval, so the frontend
    can restore the decision cards after a reload.
    """
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        return {
            "messages": service.get_messages(user["id"], conversation_id),
            **service.pending_decisions(conversation_id),
        }
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
    """Send a message and stream the assistant's turn as SSE.

    With ``edit_of_message_id`` the message replaces an earlier user
    message as a new branch version (edit/retry).
    """
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        dek = _precheck_turn(service, user, conversation_id)
        if data.edit_of_message_id is not None:
            service.check_edit_target(
                user["id"], conversation_id, data.edit_of_message_id
            )
        # Expand attached albums into their member item ids.
        album_service = AlbumService(
            album_repository=AlbumRepository(db),
            item_repository=ItemRepository(db),
            folder_repository=FolderRepository(db),
            permission_repository=PermissionRepository(db),
            item_media_repository=ItemMediaRepository(db),
        )
        expanded: List[str] = []
        for album_id in (data.album_ids or []):
            ids = album_service.list_album_item_ids(album_id, user["id"])
            if ids is None:
                raise HTTPException(
                    status_code=404, detail=f"Album not found: {album_id}"
                )
            expanded.extend(ids)
        merged = list(dict.fromkeys(list(data.item_ids or []) + expanded))
    except ConversationNotFoundError:
        raise _not_found()
    except MessageNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
    finally:
        db.close()

    def build_generator(service):
        return service.run_message_turn(
            user["id"], dek, conversation_id, data.text, merged,
            edit_of_message_id=data.edit_of_message_id,
        )

    return _sse_stream(build_generator)


@router.post("/conversations/{conversation_id}/branch")
def switch_branch(
    request: Request, conversation_id: str, data: BranchSwitchRequest
):
    """Activate the branch through a user message (version switch)."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        service.require_conversation(user["id"], conversation_id)
        if service.is_busy(user["id"]):
            raise HTTPException(
                status_code=409, detail="A chat turn is already running"
            )
        service.switch_branch(user["id"], conversation_id, data.message_id)
    except ConversationNotFoundError:
        raise _not_found()
    except MessageNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
    finally:
        db.close()
    return {"status": "ok"}


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


@router.post("/conversations/{conversation_id}/tools/{request_id}/decision")
def tool_decision(
    request: Request, conversation_id: str, request_id: str,
    data: ToolDecisionRequest,
):
    """Approve or deny a mutating tool call; streams the resumed turn."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_chat_service(db)
        service.require_conversation(user["id"], conversation_id)
        service.check_tool_confirmation(user["id"], conversation_id, request_id)
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
    except ToolConfirmationNotFoundError:
        raise HTTPException(
            status_code=404, detail="Tool confirmation not found"
        )
    except ToolConfirmationExpiredError:
        raise HTTPException(
            status_code=410, detail="Tool confirmation expired"
        )
    finally:
        db.close()

    def build_generator(service):
        return service.resume_tool_decision(
            user["id"], dek, conversation_id, request_id,
            data.approved, data.always_allow,
        )

    return _sse_stream(build_generator)
