"""Tag management routes - Phase 5: Polymorphic Items."""
import random

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..dependencies import require_user

router = APIRouter(tags=["tags"])


class TagInput(BaseModel):
    tag: str
    category_id: int | None = None


class TagPresetInput(BaseModel):
    name: str
    category_id: int


class BatchAIInput(BaseModel):
    photo_ids: list[str] = []  # Legacy: item IDs
    album_ids: list[str] = []


# =============================================================================
# Tag Categories & Presets
# =============================================================================

@router.get("/api/tag-categories")
def get_tag_categories():
    """Get all tag categories."""
    db = get_db()
    categories = db.execute("SELECT * FROM tag_categories ORDER BY id").fetchall()
    return [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in categories]


@router.get("/api/tag-presets")
def get_tag_presets(search: str = ""):
    """Get all preset tags grouped by category, optionally filtered by search."""
    db = get_db()

    if search:
        presets = db.execute("""
            SELECT p.id, p.name, p.category_id, c.name as category_name, c.color
            FROM tag_presets p
            JOIN tag_categories c ON p.category_id = c.id
            WHERE p.name LIKE ?
            ORDER BY c.id, p.name
        """, (f"%{search.lower()}%",)).fetchall()
    else:
        presets = db.execute("""
            SELECT p.id, p.name, p.category_id, c.name as category_name, c.color
            FROM tag_presets p
            JOIN tag_categories c ON p.category_id = c.id
            ORDER BY c.id, p.name
        """).fetchall()

    # Group by category
    result = {}
    for p in presets:
        cat_id = p["category_id"]
        if cat_id not in result:
            result[cat_id] = {
                "id": cat_id,
                "name": p["category_name"],
                "color": p["color"],
                "tags": []
            }
        result[cat_id]["tags"].append({"id": p["id"], "name": p["name"]})

    return list(result.values())


@router.post("/api/tag-presets")
def add_tag_preset(preset: TagPresetInput):
    """Add a new preset tag."""
    db = get_db()

    # Check if category exists
    category = db.execute(
        "SELECT id FROM tag_categories WHERE id = ?", (preset.category_id,)
    ).fetchone()
    if not category:
        raise HTTPException(status_code=400, detail="Category not found")

    # Insert preset
    try:
        db.execute(
            "INSERT INTO tag_presets (name, category_id) VALUES (?, ?)",
            (preset.name.lower().strip(), preset.category_id)
        )
        db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Tag already exists in this category")

    return {"status": "ok", "name": preset.name}


# =============================================================================
# Tag Management
# =============================================================================

@router.get("/api/tags/all")
def get_all_tags():
    """Get all unique tags for autocomplete."""
    db = get_db()
    tags = db.execute("""
        SELECT DISTINCT t.tag, t.category_id, c.color
        FROM tags t
        LEFT JOIN tag_categories c ON t.category_id = c.id
        ORDER BY t.tag
    """).fetchall()
    return [{"tag": t["tag"], "category_id": t["category_id"], "color": t["color"] or "#6b7280"} for t in tags]


@router.post("/api/items/batch-ai-tags")
def batch_generate_ai_tags(data: BatchAIInput, request: Request):
    """Generate AI tags for multiple items and albums."""
    require_user(request)
    db = get_db()

    # Get all presets
    presets = db.execute("""
        SELECT p.name, p.category_id, c.color
        FROM tag_presets p
        JOIN tag_categories c ON p.category_id = c.id
    """).fetchall()

    if not presets:
        return {"status": "error", "message": "No preset tags available"}

    # Collect all item IDs to process (individual + from albums)
    all_item_ids = list(data.photo_ids)
    for album_id in data.album_ids:
        # Phase 5: Get items from album via album_items
        album_items = db.execute(
            "SELECT item_id FROM album_items WHERE album_id = ?", (album_id,)
        ).fetchall()
        all_item_ids.extend([i["item_id"] for i in album_items])

    processed = 0
    for item_id in all_item_ids:
        # Phase 5: Check item exists
        item = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if item:
            # Select 3-6 random tags
            selected = random.sample(list(presets), min(random.randint(3, 6), len(presets)))

            # Clear existing tags and add new ones
            db.execute("DELETE FROM tags WHERE photo_id = ?", (item_id,))

            for preset in selected:
                db.execute(
                    "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
                    (item_id, preset["name"], preset["category_id"])
                )

            # Phase 5: Mark as AI processed in item_media
            db.execute("UPDATE item_media SET ai_processed = 1 WHERE item_id = ?", (item_id,))
            processed += 1

    db.commit()
    return {"status": "ok", "processed": processed}


# =============================================================================
# Search - Phase 5: Polymorphic Items
# =============================================================================

@router.get("/api/search")
def search_items_by_tags(tags: str = "", folder_id: str = None):
    """Search items and albums by tags (space-separated) within a folder.
    
    Returns full item data for rendering in gallery.
    - Only searches within the specified folder
    - Album is included if at least one item inside has ALL specified tags
    - Standalone items (not in albums) are included directly
    - Items inside albums are NOT shown separately - only the album
    """
    db = get_db()
    
    # Parse tags
    tag_list = [t.strip().lower() for t in tags.split() if t.strip()]
    
    if not tag_list or not folder_id:
        return {"items": [], "photos": [], "albums": []}
    
    placeholders = ",".join("?" * len(tag_list))
    
    # Find standalone items (not in albums) in this folder with ALL specified tags
    # Phase 5: Use items + item_media tables, exclude items in albums via album_items
    standalone_items = db.execute(f"""
        SELECT DISTINCT i.id, im.original_name, im.media_type, im.taken_at, im.uploaded_at,
               im.thumb_width, im.thumb_height, i.safe_id,
               (SELECT COUNT(*) FROM tags WHERE photo_id = i.id) as tag_count
        FROM items i
        JOIN item_media im ON i.id = im.item_id
        WHERE i.folder_id = ? 
          AND i.type = 'media'
          AND NOT EXISTS (SELECT 1 FROM album_items ai WHERE ai.item_id = i.id)
          AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = i.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY im.uploaded_at DESC
    """, (folder_id, *tag_list, len(tag_list))).fetchall()
    
    # Find albums in this folder where at least one item has ALL specified tags
    # Phase 5: Use album_items junction table
    albums = db.execute(f"""
        SELECT DISTINCT a.id, a.name, a.created_at, a.folder_id,
               (SELECT COUNT(*) FROM album_items WHERE album_id = a.id) as photo_count,
               (SELECT item_id FROM album_items WHERE album_id = a.id ORDER BY added_at DESC LIMIT 1) as cover_item_id,
               (SELECT im.thumb_width FROM album_items ai 
                JOIN item_media im ON ai.item_id = im.item_id 
                WHERE ai.album_id = a.id ORDER BY ai.added_at DESC LIMIT 1) as cover_thumb_width,
               (SELECT im.thumb_height FROM album_items ai 
                JOIN item_media im ON ai.item_id = im.item_id 
                WHERE ai.album_id = a.id ORDER BY ai.added_at DESC LIMIT 1) as cover_thumb_height,
               (SELECT i.safe_id FROM album_items ai 
                JOIN items i ON ai.item_id = i.id 
                WHERE ai.album_id = a.id ORDER BY ai.added_at DESC LIMIT 1) as safe_id
        FROM albums a
        JOIN album_items ai ON ai.album_id = a.id
        JOIN items i ON ai.item_id = i.id
        WHERE a.folder_id = ? AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = i.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY a.created_at DESC
    """, (folder_id, *tag_list, len(tag_list))).fetchall()
    
    # Format standalone items as "photo" type for frontend compatibility
    item_results = []
    for item in standalone_items:
        item_results.append({
            "type": "photo",
            "id": item["id"],
            "original_name": item["original_name"],
            "media_type": item["media_type"] or "image",
            "taken_at": item["taken_at"],
            "uploaded_at": item["uploaded_at"],
            "thumb_width": item["thumb_width"],
            "thumb_height": item["thumb_height"],
            "safe_id": item["safe_id"]
        })
    
    # Format albums
    album_results = []
    for a in albums:
        album_results.append({
            "type": "album",
            "id": a["id"],
            "name": a["name"],
            "created_at": a["created_at"],
            "photo_count": a["photo_count"],
            "cover_photo_id": a["cover_item_id"],  # Legacy alias
            "cover_item_id": a["cover_item_id"],
            "cover_thumb_width": a["cover_thumb_width"],
            "cover_thumb_height": a["cover_thumb_height"],
            "safe_id": a["safe_id"]
        })
    
    return {
        "items": item_results + album_results,  # Unified list for polymorphic frontend
        "photos": item_results,  # Legacy alias
        "albums": album_results
    }


# =============================================================================
# Legacy Endpoints - DEPRECATED, will be removed
# =============================================================================
# The following endpoints are kept for backward compatibility during transition:
# - /api/photos/{photo_id}/tag (POST) - use /api/items/{id}/tag
# - /api/photos/{photo_id}/tag/{tag_id} (DELETE) - use /api/items/{id}/tag/{tag_id}
# - /api/photos/{photo_id}/ai-tags (POST) - use /api/items/{id}/ai-tags
# - /api/photos/search - use /api/search
# - /api/photos/batch-ai-tags - use /api/items/batch-ai-tags
# 
# TODO: Remove these in v1.1
