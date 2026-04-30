"""AI service API routes - job queue for external AI agents."""
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from ..database import create_connection
from ..dependencies import require_user, require_api_key
from ..infrastructure.repositories import (
    AIJobRepository,
    TagsRepository,
    TagImplicationRepository,
    TagCooccurrenceRepository,
    ItemRepository,
    ItemMediaRepository,
)
from ..infrastructure.storage import get_storage, LocalStorage
from ..application.services import TagService, AITaggingService

router = APIRouter(tags=["ai"])

storage = get_storage()


# =============================================================================
# Schemas
# =============================================================================

class CreateJobsInput(BaseModel):
    item_ids: List[str]


class JobResultInput(BaseModel):
    tag_ids: List[int]


class JobFailInput(BaseModel):
    error: str


# =============================================================================
# Helpers
# =============================================================================

def _tag_service(db):
    """Build TagService with all repositories."""
    return TagService(
        TagsRepository(db),
        TagImplicationRepository(db),
        TagCooccurrenceRepository(db),
    )


def _ai_tagging_service(db):
    """Build AITaggingService with all repositories."""
    return AITaggingService(
        AIJobRepository(db),
        _tag_service(db),
        ItemRepository(db),
        ItemMediaRepository(db),
    )


# =============================================================================
# User-facing endpoints (session auth)
# =============================================================================

@router.post("/api/ai/jobs")
def create_jobs(data: CreateJobsInput, request: Request):
    """Create AI tagging jobs for selected items."""
    require_user(request)
    if not data.item_ids:
        raise HTTPException(status_code=400, detail="No item IDs provided")

    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        jobs = service.create_jobs(data.item_ids)
        return {"jobs": jobs}
    finally:
        db.close()


@router.get("/api/ai/jobs/progress")
def get_progress(request: Request, job_ids: str = Query(...)):
    """Check progress of AI tagging jobs."""
    require_user(request)
    if not job_ids.strip():
        raise HTTPException(status_code=400, detail="No job IDs provided")

    try:
        ids = [int(x.strip()) for x in job_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job IDs format")

    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        stats = service.get_job_progress(ids)
        return stats
    finally:
        db.close()


# =============================================================================
# Agent-facing endpoints (API key auth)
# =============================================================================

@router.get("/api/ai/jobs/pending")
def get_pending_jobs(
    request: Request,
    limit: int = Query(10, ge=1, le=50)
):
    """Get pending jobs for AI agents."""
    require_api_key(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        jobs = service.get_pending_jobs(limit)
        return {"jobs": jobs}
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/claim")
def claim_job(job_id: int, request: Request):
    """Atomically claim a pending job."""
    require_api_key(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        result = service.claim_job(job_id)
        if not result:
            raise HTTPException(status_code=409, detail="Job not available")
        return result
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/results")
def submit_results(job_id: int, data: JobResultInput, request: Request):
    """Submit tag results for a job."""
    require_api_key(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        success = service.submit_results(job_id, data.tag_ids)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to complete job")
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/fail")
def fail_job(job_id: int, data: JobFailInput, request: Request):
    """Mark a job as failed."""
    require_api_key(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        success = service.fail_job(job_id, data.error)
        if not success:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "ok"}
    finally:
        db.close()


# =============================================================================
# File access for AI agents
# =============================================================================

@router.get("/api/ai/items/{item_id}/file")
async def get_item_file_api(item_id: str, request: Request):
    """Get file for AI agent analysis.

    Returns decrypted file bytes for non-encrypted items.
    Server-side encrypted items are not accessible via API key.
    """
    require_api_key(request)

    db = create_connection()
    try:
        item_repo = ItemRepository(db)
        media_repo = ItemMediaRepository(db)

        item = item_repo.get_by_id(item_id)
        if not item or item.get("type") != "media":
            raise HTTPException(status_code=404, detail="Item not found")

        # Do not serve encrypted items via API key (no DEK available)
        if item.get("is_encrypted") or item.get("safe_id"):
            raise HTTPException(status_code=403, detail="Encrypted items not accessible via API")

        media = media_repo.get_by_item_id(item_id)
        content_type = media.get("content_type") if media else "image/jpeg"

        if not storage.exists(item_id, "uploads"):
            raise HTTPException(status_code=404, detail="File not found")

        if isinstance(storage, LocalStorage):
            from fastapi.responses import FileResponse
            file_path = storage.get_path(item_id, "uploads")
            return FileResponse(file_path, media_type=content_type)
        else:
            from fastapi.responses import RedirectResponse
            url = storage.get_url(item_id, "uploads", expires=3600)
            return RedirectResponse(url=url)
    finally:
        db.close()
