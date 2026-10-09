"""Per-user AI provider settings routes - providers, models, chat settings.

API keys are stored encrypted with the user's DEK, so every endpoint
requires both an authenticated user and a DEK available in the session
cache (403 otherwise).
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..application.services.ai_provider_service import AiProviderService
from ..database import create_connection
from ..dependencies import require_user
from ..infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)
from ..infrastructure.repositories.ai_model_repository import AiModelRepository
from ..infrastructure.repositories.ai_provider_repository import (
    AiProviderRepository,
)
from ..infrastructure.services.encryption import dek_cache

router = APIRouter(prefix="/api/user/ai", tags=["ai-settings"])


def build_ai_provider_service(db) -> AiProviderService:
    """Create AiProviderService with its repositories on one connection."""
    return AiProviderService(
        provider_repo=AiProviderRepository(db),
        model_repo=AiModelRepository(db),
        settings_repo=AiChatSettingsRepository(db),
    )


def _require_dek(request: Request):
    """Require an authenticated user with a DEK in the session cache.

    Returns:
        Tuple (user, dek).

    Raises:
        HTTPException: 403 when the encryption key is not available.
    """
    user = require_user(request)
    dek = dek_cache.get(user["id"])
    if dek is None:
        raise HTTPException(
            status_code=403, detail="Encryption key not available"
        )
    return user, dek


# ============================================================================
# Request bodies
# ============================================================================

class CreateProviderRequest(BaseModel):
    label: str = Field(min_length=1)
    protocol: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class UpdateProviderRequest(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1)
    protocol: Optional[str] = Field(default=None, min_length=1)
    base_url: Optional[str] = Field(default=None, min_length=1)
    # None or "" means "keep the existing key", so no min_length here.
    api_key: Optional[str] = None


class UpdateSettingsRequest(BaseModel):
    active_provider_id: Optional[int] = None
    active_model_id: Optional[str] = None


# ============================================================================
# Providers
# ============================================================================

@router.get("/providers")
def list_providers(request: Request):
    """List the user's AI providers (keys masked, never the raw blob)."""
    user, _dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        return {"providers": service.list_providers(user["id"])}
    finally:
        db.close()


@router.post("/providers")
def create_provider(request: Request, data: CreateProviderRequest):
    """Create an AI provider with a DEK-encrypted API key."""
    user, dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        provider = service.create_provider(
            user_id=user["id"],
            label=data.label,
            protocol=data.protocol,
            base_url=data.base_url,
            api_key=data.api_key,
            dek=dek,
        )
        return {"provider": provider}
    finally:
        db.close()


@router.put("/providers/{provider_id}")
def update_provider(request: Request, provider_id: int, data: UpdateProviderRequest):
    """Update an AI provider. An empty api_key keeps the stored key."""
    user, dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        provider = service.update_provider(
            user["id"],
            provider_id,
            label=data.label,
            protocol=data.protocol,
            base_url=data.base_url,
            api_key=data.api_key,
            dek=dek,
        )
        return {"provider": provider}
    finally:
        db.close()


@router.delete("/providers/{provider_id}")
def delete_provider(request: Request, provider_id: int):
    """Delete an AI provider along with its models and active selection."""
    user, _dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        if not service.delete_provider(user["id"], provider_id):
            raise HTTPException(status_code=404, detail="Provider not found")
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/providers/{provider_id}/fetch-models")
async def fetch_models(request: Request, provider_id: int):
    """Refresh the model catalogue from the provider API."""
    user, dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        models = await service.fetch_models(user["id"], provider_id, dek)
        return {"models": models, "fetched": len(models)}
    finally:
        db.close()


# ============================================================================
# Chat settings
# ============================================================================

@router.get("/settings")
def get_settings(request: Request):
    """Return the user's chat settings with provider/model info."""
    user, _dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        return {"settings": service.get_settings(user["id"])}
    finally:
        db.close()


@router.put("/settings")
def update_settings(request: Request, data: UpdateSettingsRequest):
    """Set the active provider/model pair for the AI chat."""
    user, _dek = _require_dek(request)

    db = create_connection()
    try:
        service = build_ai_provider_service(db)
        settings = service.update_settings(
            user["id"],
            data.active_provider_id,
            data.active_model_id,
        )
        return {"settings": settings}
    finally:
        db.close()
