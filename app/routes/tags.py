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
    TagMutexRepository,
    TagFeedbackRepository,
    PermissionRepository,
    FolderRepository,
    ItemRepository,
    ItemMediaRepository,
    SafeRepository,
    AlbumRepository,
)
from ..application.services import TagService, TagSuggestionService, PermissionService

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


class TagFeedbackInput(BaseModel):
    item_id: str
    context_tag_ids: List[int]
    suggested_tag_id: int
    outcome: str  # accepted | rejected | dismissed


# =============================================================================
# Helpers
# =============================================================================

def _tag_service(db):
    """Build TagService with all v3 repositories."""
    return TagService(
        TagsRepository(db),
        TagImplicationRepository(db),
        TagCooccurrenceRepository(db),
        TagMutexRepository(db),
    )


def _permission_service(db):
    """Build PermissionService with required repositories."""
    return PermissionService(
        PermissionRepository(db),
        FolderRepository(db),
        ItemRepository(db),
        safe_repository=SafeRepository(db),
    )


def _suggestion_service(db):
    """Build TagSuggestionService with all repositories."""
    return TagSuggestionService(
        TagCooccurrenceRepository(db),
        TagsRepository(db),
        TagMutexRepository(db),
        TagFeedbackRepository(db),
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


@router.get("/api/items/{item_id}/tags/suggestions")
def get_item_tag_suggestions(item_id: str, request: Request):
    """Get contextual tag suggestions for an item."""
    require_user(request)
    db = create_connection()
    try:
        service = _suggestion_service(db)
        return {"tags": service.get_suggestions_for_item(item_id)}
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


class BulkTagEditInput(BaseModel):
    item_ids: List[str]
    add_tag_ids: List[int] = []
    remove_tag_ids: List[int] = []


@router.get("/api/items/tags/common")
def get_common_tags(
    request: Request,
    item_ids: str = Query(..., description="Comma-separated item IDs")
):
    """Get tags common to all specified items."""
    require_user(request)
    ids = [i.strip() for i in item_ids.split(",") if i.strip()]
    if not ids:
        return {"tags": []}

    db = create_connection()
    try:
        service = _tag_service(db)
        tags = service.get_common_tags(ids)
        return {"tags": tags}
    finally:
        db.close()


@router.post("/api/items/tags/bulk")
def bulk_edit_tags(data: BulkTagEditInput, request: Request):
    """Add/remove tags from multiple items. Skips items without edit permission."""
    user = require_user(request)
    db = create_connection()
    try:
        tag_service = _tag_service(db)
        perm_service = _permission_service(db)
        result = tag_service.bulk_edit_tags(
            data.item_ids,
            data.add_tag_ids,
            data.remove_tag_ids,
            perm_service,
            user["id"],
        )
        return {"status": "ok", **result}
    finally:
        db.close()


@router.post("/api/tag-feedback")
def submit_tag_feedback(data: TagFeedbackInput, request: Request):
    """Record user feedback for a tag suggestion."""
    require_user(request)
    if data.outcome not in ("accepted", "rejected", "dismissed"):
        raise HTTPException(400, "Invalid outcome")
    db = create_connection()
    try:
        service = _suggestion_service(db)
        service.record_feedback(
            data.item_id,
            data.context_tag_ids,
            data.suggested_tag_id,
            data.outcome,
        )
        return {"status": "ok"}
    finally:
        db.close()


# =============================================================================
# Search
# =============================================================================

@router.get("/api/search")
def search_by_tags(
    request: Request,
    tags: str = "",
    folder_id: Optional[str] = None,
    sort: str = Query("uploaded", pattern="^(uploaded|taken)$")
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
        tag_service = _tag_service(db)
        result = tag_service.search_items(tags, folder_id, sort)

        # Merge albums that contain matching items
        matching_items = result.get("items", [])
        matching_ids = [item["id"] for item in matching_items]

        tags_repo = TagsRepository(db)
        albums = tags_repo.get_albums_for_tag_search(matching_ids)

        # Collect cover IDs and fetch thumbnail dimensions in batch
        album_item_ids = set()
        album_builders = []
        for album in albums:
            album_ids = tags_repo.get_album_item_ids(album["id"])
            matching_in_album = [iid for iid in album_ids if iid in matching_ids]
            album_item_ids.update(matching_in_album)

            cover_id = album.get("cover_item_id")
            if cover_id not in matching_in_album:
                cover_id = matching_in_album[0] if matching_in_album else None

            album_builders.append({
                "album": album,
                "matching_in_album": matching_in_album,
                "cover_id": cover_id,
            })

        # Batch fetch cover thumbnail dimensions
        cover_ids = [b["cover_id"] for b in album_builders if b["cover_id"]]
        cover_dims = {}
        if cover_ids:
            media_repo = ItemMediaRepository(db)
            placeholders = ','.join('?' * len(cover_ids))
            cursor = db.execute(
                f"SELECT item_id, thumb_width, thumb_height FROM item_media WHERE item_id IN ({placeholders})",
                tuple(cover_ids)
            )
            for row in cursor.fetchall():
                cover_dims[row["item_id"]] = {
                    "thumb_width": row["thumb_width"],
                    "thumb_height": row["thumb_height"],
                }

        album_results = []
        for builder in album_builders:
            album = builder["album"]
            cover_id = builder["cover_id"]
            dims = cover_dims.get(cover_id, {})
            album_results.append({
                "type": "album",
                "id": album["id"],
                "name": album["name"],
                "photo_count": len(builder["matching_in_album"]),
                "cover_photo_id": cover_id,
                "cover_item_id": cover_id,
                "cover_thumb_width": dims.get("thumb_width"),
                "cover_thumb_height": dims.get("thumb_height"),
                "safe_id": album.get("safe_id"),
                "matching_item_ids": builder["matching_in_album"],
                "uploaded_at": album.get("uploaded_at"),
                "taken_at": album.get("taken_at"),
            })

        # Standalone items = those not in any album
        standalone_items = [item for item in matching_items if item["id"] not in album_item_ids]

        # Combine: albums first, then standalone items
        result["items"] = album_results + standalone_items
        result["total"] = len(result["items"])
        return result
    finally:
        db.close()
