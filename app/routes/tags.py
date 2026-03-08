"""Tag management routes - Hierarchical Tags v2."""
from typing import List, Optional

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from ..database import create_connection
from ..dependencies import require_user
from ..infrastructure.repositories import TagsRepository
from ..application.services import TagService

router = APIRouter(tags=["tags"])


# =============================================================================
# Schemas
# =============================================================================

class TagCreateInput(BaseModel):
    name: str
    display_name: Optional[str] = None
    category_id: int
    parent_id: Optional[int] = None


class TagAddInput(BaseModel):
    tag_id: int


class BatchTagInput(BaseModel):
    item_ids: List[str]
    add_tag_ids: List[int] = []
    remove_tag_ids: List[int] = []


# =============================================================================
# Categories
# =============================================================================

@router.get("/api/tag-categories")
def get_tag_categories():
    """Get all tag categories."""
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        return {"categories": service.get_categories()}
    finally:
        db.close()


# =============================================================================
# Tag Tree & Search
# =============================================================================

@router.get("/api/tags/search")
def search_tags(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50)
):
    """Search tags with usage count."""
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        return {"tags": service.search_tags(q, limit)}
    finally:
        db.close()


@router.get("/api/tags/tree")
def get_tag_tree(
    category: Optional[str] = None,
    parent_id: Optional[int] = None
):
    """Get tag tree for browsing.
    
    Args:
        category: Category slug (optional)
        parent_id: Parent tag ID (optional)
    """
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        return service.get_tree(category, parent_id)
    finally:
        db.close()


@router.get("/api/tags/{tag_id}")
def get_tag(tag_id: int):
    """Get tag with full hierarchy."""
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        return service.get_tag_with_hierarchy(tag_id)
    finally:
        db.close()


@router.post("/api/tags")
def create_tag(data: TagCreateInput, request: Request):
    """Create a new tag."""
    require_user(request)
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        tag = service.create_tag(
            name=data.name,
            display_name=data.display_name,
            category_id=data.category_id,
            parent_id=data.parent_id
        )
        return {"status": "ok", "tag": tag}
    finally:
        db.close()


# =============================================================================
# Item Tags
# =============================================================================

@router.get("/api/items/{item_id}/tags")
def get_item_tags(item_id: str, request: Request):
    """Get all tags for an item."""
    require_user(request)
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        return service.get_item_tags(item_id)
    finally:
        db.close()


@router.post("/api/items/{item_id}/tags")
def add_tag_to_item(item_id: str, data: TagAddInput, request: Request):
    """Add tag to item (with ancestors)."""
    require_user(request)
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        result = service.add_tag_to_item(item_id, data.tag_id)
        return {"status": "ok", **result}
    finally:
        db.close()


@router.delete("/api/items/{item_id}/tags/{tag_id}")
def remove_tag_from_item(
    item_id: str, 
    tag_id: int,
    request: Request
):
    """Remove explicit tag from item (inherited tags recalculate automatically)."""
    require_user(request)
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
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
        service = TagService(TagsRepository(db))
        result = service.search_items(tags, folder_id)
        return result
    finally:
        db.close()


# =============================================================================
# Bulk Operations
# =============================================================================

@router.post("/api/items/batch-tags")
def batch_tag_items(data: BatchTagInput, request: Request):
    """Batch add/remove tags from items."""
    require_user(request)
    if not data.item_ids:
        raise HTTPException(400, "No items specified")
    
    db = create_connection()
    try:
        service = TagService(TagsRepository(db))
        result = service.batch_tag_items(
            data.item_ids,
            data.add_tag_ids,
            data.remove_tag_ids
        )
        return {"status": "ok", **result}
    finally:
        db.close()


