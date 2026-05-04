"""Tag management routes - Flat Tags v3 with implications."""
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from ..database import create_connection
from ..dependencies import require_user, require_admin
from ..infrastructure.repositories import (
    TagsRepository,
    TagImplicationRepository,
    TagCooccurrenceRepository,
)
from ..application.services import TagService

router = APIRouter(tags=["tags"])


# =============================================================================
# Schemas
# =============================================================================

class TagCreateInput(BaseModel):
    name: str
    display_name: Optional[str] = None
    category_id: int
    description: Optional[str] = ''


class TagAddInput(BaseModel):
    tag_id: int


class TagSetInput(BaseModel):
    tag_ids: List[int]


# =============================================================================
# Helpers
# =============================================================================

def _tag_service(db):
    """Build TagService with all v3 repositories."""
    return TagService(
        TagsRepository(db),
        TagImplicationRepository(db),
        TagCooccurrenceRepository(db),
    )


# =============================================================================
# Categories
# =============================================================================

@router.get("/api/tag-categories")
def get_tag_categories():
    """Get all tag categories."""
    db = create_connection()
    try:
        service = _tag_service(db)
        return {"categories": service.get_categories()}
    finally:
        db.close()


# =============================================================================
# Tag Search & Info
# =============================================================================

@router.get("/api/tags/search")
def search_tags(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100)
):
    """Search tags with usage count."""
    db = create_connection()
    try:
        service = _tag_service(db)
        return {"tags": service.search_tags(q, limit)}
    finally:
        db.close()


@router.get("/api/tags/{tag_id}")
def get_tag(tag_id: int):
    """Get tag by ID."""
    db = create_connection()
    try:
        service = _tag_service(db)
        tag = service.get_tag(tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        return tag
    finally:
        db.close()


@router.get("/api/tags/{tag_id}/related")
def get_related_tags(
    tag_id: int,
    limit: int = Query(10, ge=1, le=50)
):
    """Get tags frequently co-occurring with this tag."""
    db = create_connection()
    try:
        service = _tag_service(db)
        return {"tags": service.get_related_tags(tag_id, limit)}
    finally:
        db.close()


@router.get("/api/tags/{tag_id}/implications")
def get_tag_implications(tag_id: int):
    """Get implication graph for a tag."""
    db = create_connection()
    try:
        service = _tag_service(db)
        return service.get_tag_implications(tag_id)
    finally:
        db.close()


@router.post("/api/tags")
def create_tag(data: TagCreateInput, request: Request):
    """Create a new tag."""
    require_admin(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        tag = service.create_tag(
            name=data.name,
            display_name=data.display_name,
            category_id=data.category_id,
            description=data.description or '',
        )
        return {"status": "ok", "tag": tag}
    finally:
        db.close()


# =============================================================================
# Item Tags
# =============================================================================

@router.get("/api/items/{item_id}/tags")
def get_item_tags(item_id: str, request: Request):
    """Get all tags for an item (explicit + implied)."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        return service.get_item_tags(item_id)
    finally:
        db.close()


@router.post("/api/items/{item_id}/tags")
def add_tag_to_item(item_id: str, data: TagAddInput, request: Request):
    """Add a single explicit tag to item."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        result = service.add_tag_to_item(item_id, data.tag_id)
        return {"status": "ok", **result}
    finally:
        db.close()


@router.put("/api/items/{item_id}/tags")
def set_item_tags(item_id: str, data: TagSetInput, request: Request):
    """Replace all explicit tags for item (implied resolved automatically)."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        result = service.set_item_tags(item_id, data.tag_ids)
        return {"status": "ok", **result}
    finally:
        db.close()


@router.delete("/api/items/{item_id}/tags/{tag_id}")
def remove_tag_from_item(
    item_id: str,
    tag_id: int,
    request: Request
):
    """Remove explicit tag from item (implied tags recalculate automatically)."""
    require_user(request)
    db = create_connection()
    try:
        service = _tag_service(db)
        result = service.remove_tag_from_item(item_id, tag_id)
        return {"status": "ok", **result}
    finally:
        db.close()


# =============================================================================
# Search
# =============================================================================

@router.get("/api/search")
def search_by_tags(
    request: Request,
    tags: str = "",
    folder_id: Optional[str] = None
):
    """Search items by tags with negative support.

    Query syntax:
    - "fox night" - items with fox AND night
    - "fox -wolf" - items with fox but NOT wolf
    - "animal -mammal forest" - items with animal AND forest, not mammal
    """
    require_user(request)
    if not tags.strip():
        return {"items": [], "include": [], "exclude": [], "total": 0}

    db = create_connection()
    try:
        service = _tag_service(db)
        result = service.search_items(tags, folder_id)
        return result
    finally:
        db.close()
