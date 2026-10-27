'''
File:   admin_tags.py
Brief:  Tag management page routes - admin CRUD and implications.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0
'''
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..config import ROOT_PATH
from ..database import create_connection
from ..dependencies import require_user, require_admin
from ..infrastructure.repositories import TagsRepository, TagImplicationRepository, TagCooccurrenceRepository, TagMutexRepository
from ..application.services import TagService

router = APIRouter()


class TagUpdateInput(BaseModel):
    name: Optional[str] = None
    category_id: Optional[int] = None
    description: Optional[str] = None


class ImplicationInput(BaseModel):
    implies_tag_id: int


class TagRemapInput(BaseModel):
    target_tag_id: int


class CategoryCreateInput(BaseModel):
    name: str
    color: str = "#888888"
    sort_order: Optional[int] = None


class CategoryUpdateInput(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


def _tag_service(db):
    return TagService(
        TagsRepository(db),
        TagImplicationRepository(db),
        TagCooccurrenceRepository(db),
        TagMutexRepository(db),
    )


# =============================================================================
# Page
# =============================================================================

@router.get("/tags")
def tags_page(request: Request):
    """Legacy standalone /tags page: redirect to the in-gallery tags view."""
    require_user(request)
    tag = request.query_params.get("tag")
    url = f"{ROOT_PATH}/?view=tags"
    if tag:
        url += "&tag=" + quote(tag, safe="")
    return RedirectResponse(url, status_code=302)


# =============================================================================
# Tag CRUD API (extensions not present in routes/tags.py)
# =============================================================================

@router.get("/api/tags")
def list_tags(
    request: Request,
    q: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Paginated tag list with implication counts."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        return service.list_tags(q, limit, offset, category_id)
    finally:
        db.close()


@router.put("/api/tags/{tag_id}")
def update_tag(tag_id: int, data: TagUpdateInput, request: Request):
    """Update tag fields."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        tag = service.update_tag(
            tag_id,
            name=data.name,
            category_id=data.category_id,
            description=data.description
        )
        return {"status": "ok", "tag": tag}
    finally:
        db.close()


@router.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, request: Request):
    """Delete a tag."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        deleted = service.delete_tag(tag_id)
        return {"status": "ok", "deleted": deleted}
    finally:
        db.close()


@router.post("/api/tags/{tag_id}/remap")
def remap_tag(tag_id: int, data: TagRemapInput, request: Request):
    """Delete a tag and remap all its items to another tag."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        service.remap_tag(tag_id, data.target_tag_id)
        return {"status": "ok", "remapped": True}
    finally:
        db.close()


@router.post("/api/tags/{tag_id}/replace")
def replace_tag(tag_id: int, data: TagRemapInput, request: Request):
    """Replace a tag with another on all items without deleting the source."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        service.replace_tag(tag_id, data.target_tag_id)
        return {"status": "ok", "replaced": True}
    finally:
        db.close()


# =============================================================================
# Implication API (extensions not present in routes/tags.py)
# =============================================================================

@router.post("/api/tags/{tag_id}/implications")
def add_implication(tag_id: int, data: ImplicationInput, request: Request):
    """Add implication edge: tag_id -> implies_tag_id."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        try:
            result = service.create_implication(tag_id, data.implies_tag_id)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))
        return {"status": "ok", "implications": result}
    finally:
        db.close()


@router.delete("/api/tags/{tag_id}/implications/{implies_tag_id}")
def remove_implication(tag_id: int, implies_tag_id: int, request: Request):
    """Remove implication edge."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        deleted = service.delete_implication(tag_id, implies_tag_id)
        return {"status": "ok", "deleted": deleted}
    finally:
        db.close()


# =============================================================================
# Category CRUD API
# =============================================================================

@router.get("/api/tag-categories")
def list_categories(request: Request):
    """Get all tag categories."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        return {"categories": service.get_categories()}
    finally:
        db.close()


@router.post("/api/tag-categories")
def create_category(data: CategoryCreateInput, request: Request):
    """Create a new tag category."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        cat = service.create_category(data.name, data.color, data.sort_order)
        return {"status": "ok", "category": cat}
    finally:
        db.close()


@router.put("/api/tag-categories/{category_id}")
def update_category(category_id: int, data: CategoryUpdateInput, request: Request):
    """Update a tag category."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        cat = service.update_category(category_id, name=data.name, color=data.color, sort_order=data.sort_order)
        return {"status": "ok", "category": cat}
    finally:
        db.close()


@router.delete("/api/tag-categories/{category_id}")
def delete_category(category_id: int, request: Request):
    """Delete a tag category."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        deleted = service.delete_category(category_id)
        return {"status": "ok", "deleted": deleted}
    finally:
        db.close()


@router.post("/api/admin/tags/sanitize")
def sanitize_tags(request: Request):
    """Recalculate implied tags for all items. Admin only."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        result = service.sanitize_all_items()
        return {
            "status": "ok",
            "items_processed": result["updated"],
            "tags_added": result["tags_added"],
            "tags_removed": result["tags_removed"],
            "stats_rebuilt": True,
        }
    finally:
        db.close()


@router.post("/api/admin/tags/rebuild-mutex")
def rebuild_mutex(request: Request):
    """Rebuild mutex pair statistics. Admin only."""
    require_admin(request)
    db = create_connection()
    try:
        repo = TagMutexRepository(db)
        repo.rebuild_all()
        return {"status": "ok", "rebuilt": True}
    finally:
        db.close()
