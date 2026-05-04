"""AI service API routes - job queue for external AI agents."""
import asyncio
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..database import create_connection
from ..dependencies import require_user, require_api_key, require_admin, _check_rate_limit
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
from ..infrastructure.services.audit_log import log_ai_job_claimed

router = APIRouter(tags=["ai"])

storage = get_storage()


# =============================================================================
# Schemas
# =============================================================================

class CreateJobsInput(BaseModel):
    item_ids: List[str]


class JobResultInput(BaseModel):
    tag_ids: Optional[List[int]] = Field(None, max_length=50)
    tag_names: Optional[List[str]] = Field(None, max_length=50)


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
    user = require_user(request)
    if not data.item_ids:
        raise HTTPException(status_code=400, detail="No item IDs provided")

    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        jobs = service.create_jobs(data.item_ids, user["id"])
        return {"jobs": jobs}
    finally:
        db.close()


@router.get("/api/ai/jobs/active")
def get_active_jobs(request: Request):
    """Get active jobs for the current user (for page load recovery)."""
    user = require_user(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        jobs = service.get_active_jobs_for_user(user["id"])
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


@router.get("/api/ai/jobs/events")
async def job_events(request: Request):
    """Server-Sent Events stream for job progress updates.

    Sends progress updates every 2 seconds for the user's active jobs.
    """
    user = require_user(request)

    async def event_generator():
        sent_progress = {}
        while True:
            db = create_connection()
            try:
                service = _ai_tagging_service(db)
                jobs = service.get_active_jobs_for_user(user["id"])

                if not jobs:
                    # No active jobs, send keepalive and close after a bit
                    yield f"event: ping\ndata: {json.dumps({'time': asyncio.get_event_loop().time()})}\n\n"
                    await asyncio.sleep(5)
                    # Double-check before closing
                    jobs = service.get_active_jobs_for_user(user["id"])
                    if not jobs:
                        yield f"event: done\ndata: {json.dumps({'message': 'No active jobs'})}\n\n"
                        break
                    continue

                job_ids = [j["id"] for j in jobs]
                stats = service.get_job_progress(job_ids)

                # Only send if progress changed
                progress_key = f"{stats.get('completed', 0)}-{stats.get('failed', 0)}-{stats.get('processing', 0)}-{stats.get('pending', 0)}"
                if progress_key != sent_progress.get(tuple(job_ids)):
                    sent_progress[tuple(job_ids)] = progress_key
                    # Serialize jobs without datetime objects
                    serializable_jobs = []
                    for j in jobs:
                        sj = dict(j)
                        for key in list(sj.keys()):
                            if isinstance(sj[key], datetime):
                                sj[key] = sj[key].isoformat()
                        serializable_jobs.append(sj)
                    payload = {
                        "job_ids": job_ids,
                        "stats": stats,
                        "jobs": serializable_jobs,
                    }
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"

                # If all done, send completion and close
                done = stats.get("completed", 0) + stats.get("failed", 0)
                if done >= stats["total"]:
                    yield f"event: complete\ndata: {json.dumps(stats)}\n\n"
                    break

            finally:
                db.close()

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# =============================================================================
# Agent-facing endpoints (API key auth)
# =============================================================================

@router.get("/api/ai/jobs/pending")
def get_pending_jobs(
    request: Request,
    limit: int = Query(10, ge=1, le=50)
):
    """Get pending jobs for AI agents (scoped to API key owner)."""
    api_key_info = require_api_key(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        jobs = service.get_pending_jobs_for_user(api_key_info["user_id"], limit)
        return {"jobs": jobs}
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/claim")
def claim_job(job_id: int, request: Request):
    """Atomically claim a pending job."""
    api_key_info = require_api_key(request)
    db = create_connection()
    try:
        # Verify job belongs to API key owner before claiming
        job_repo = AIJobRepository(db)
        job = job_repo.get_job_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["user_id"] != api_key_info["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        service = _ai_tagging_service(db)
        result = service.claim_job(job_id)
        if not result:
            raise HTTPException(status_code=409, detail="Job not available")

        log_ai_job_claimed(
            key_id=api_key_info["id"],
            job_id=job_id,
            item_id=result["job"]["item_id"]
        )
        return result
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/results")
def submit_results(job_id: int, data: JobResultInput, request: Request):
    """Submit tag results for a job. Accepts tag_ids or tag_names."""
    api_key_info = require_api_key(request)
    db = create_connection()
    try:
        # Verify job belongs to API key owner
        job_repo = AIJobRepository(db)
        job = job_repo.get_job_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["user_id"] != api_key_info["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

        service = _ai_tagging_service(db)
        success = service.submit_results(job_id, tag_ids=data.tag_ids, tag_names=data.tag_names)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to complete job")
        return {"status": "ok"}
    except HTTPException:
        raise
    finally:
        db.close()


@router.get("/api/ai/tags")
def get_ai_tags(request: Request):
    """Get all tags for AI agent reference."""
    require_api_key(request)
    db = create_connection()
    try:
        tag_repo = TagsRepository(db)
        tags = tag_repo.list_tags(query=None, limit=10000, offset=0)
        simplified = [
            {
                "id": t["id"],
                "name": t["name"],
                "display_name": t.get("display_name"),
                "description": t.get("description"),
                "category": t.get("category_name"),
            }
            for t in tags
        ]
        return {"tags": simplified, "total": len(simplified)}
    finally:
        db.close()


@router.post("/api/ai/jobs/{job_id}/fail")
def fail_job(job_id: int, data: JobFailInput, request: Request):
    """Mark a job as failed."""
    api_key_info = require_api_key(request)
    db = create_connection()
    try:
        # Verify job belongs to API key owner
        job_repo = AIJobRepository(db)
        job = job_repo.get_job_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["user_id"] != api_key_info["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

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
    api_key_info = require_api_key(request)

    # Apply stricter rate limit for file downloads (30/min per API key)
    import hashlib
    rate_key = hashlib.sha256(str(api_key_info["id"]).encode()).hexdigest() + ":file"
    if not _check_rate_limit(rate_key, max_requests=30, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for file downloads",
            headers={"Retry-After": "60"}
        )

    db = create_connection()
    try:
        item_repo = ItemRepository(db)
        media_repo = ItemMediaRepository(db)

        item = item_repo.get_by_id(item_id)
        if not item or item.get("type") != "media":
            raise HTTPException(status_code=404, detail="Item not found")

        # Enforce ownership: item must belong to the API key owner
        if item.get("user_id") != api_key_info["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")

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


# =============================================================================
# Reaper / Maintenance
# =============================================================================

@router.post("/api/ai/jobs/reap")
def reap_jobs(request: Request, max_retries: int = Query(3, ge=1, le=10)):
    """Reap stale processing jobs. Admin only."""
    require_admin(request)
    db = create_connection()
    try:
        service = _ai_tagging_service(db)
        result = service.reap_stale_jobs(max_retries)
        return result
    finally:
        db.close()
