"""Tag management routes."""
import random

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..database import get_db
from ..dependencies import require_user

router = APIRouter(tags=["tags"])


class TagInput(BaseModel):
    tag: str
    category_id: int


class TagPresetInput(BaseModel):
    name: str
    category_id: int


class BatchAIInput(BaseModel):
    photo_ids: list[str] = []
    album_ids: list[str] = []


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


@router.post("/api/photos/{photo_id}/tag")
def add_tag_to_photo(photo_id: str, tag_input: TagInput, request: Request):
    """Add a single tag to a photo."""
    require_user(request)
    db = get_db()

    # Check if photo exists
    photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Check if tag already exists for this photo
    existing = db.execute(
        "SELECT id FROM tags WHERE photo_id = ? AND tag = ?",
        (photo_id, tag_input.tag.lower().strip())
    ).fetchone()
    if existing:
        return {"status": "exists", "message": "Tag already added"}

    # Add tag
    cursor = db.execute(
        "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
        (photo_id, tag_input.tag.lower().strip(), tag_input.category_id)
    )
    db.commit()

    # Get category info
    category = db.execute(
        "SELECT name, color FROM tag_categories WHERE id = ?",
        (tag_input.category_id,)
    ).fetchone()

    return {
        "status": "ok",
        "tag": {
            "id": cursor.lastrowid,
            "tag": tag_input.tag.lower().strip(),
            "category_id": tag_input.category_id,
            "category": category["name"] if category else None,
            "color": category["color"] if category else "#6b7280"
        }
    }


@router.delete("/api/photos/{photo_id}/tag/{tag_id}")
def remove_tag_from_photo(photo_id: str, tag_id: int, request: Request):
    """Remove a tag from a photo."""
    require_user(request)
    db = get_db()

    db.execute(
        "DELETE FROM tags WHERE id = ? AND photo_id = ?",
        (tag_id, photo_id)
    )
    db.commit()

    return {"status": "ok"}


@router.post("/api/photos/{photo_id}/ai-tags")
def generate_ai_tags(photo_id: str, request: Request):
    """Generate random tags from presets (simulates AI tagging)."""
    require_user(request)
    db = get_db()

    # Check if photo exists
    photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Get random presets from different categories
    presets = db.execute("""
        SELECT p.name, p.category_id, c.color
        FROM tag_presets p
        JOIN tag_categories c ON p.category_id = c.id
    """).fetchall()

    if not presets:
        return {"status": "error", "message": "No preset tags available"}

    # Select 3-6 random tags
    selected = random.sample(list(presets), min(random.randint(3, 6), len(presets)))

    # Clear existing tags and add new ones
    db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))

    added_tags = []
    for preset in selected:
        cursor = db.execute(
            "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
            (photo_id, preset["name"], preset["category_id"])
        )
        added_tags.append({
            "id": cursor.lastrowid,
            "tag": preset["name"],
            "category_id": preset["category_id"],
            "color": preset["color"]
        })

    db.commit()

    return {"status": "ok", "tags": added_tags}


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


@router.get("/api/photos/search")
def search_photos_by_tags(tags: str = "", request: Request = None):
    """Search photos and albums by tags (space-separated)."""
    db = get_db()

    if not tags.strip():
        # Return all standalone photos and albums if no tags specified
        photos = db.execute(
            "SELECT id, 'photo' as type FROM photos WHERE album_id IS NULL ORDER BY uploaded_at DESC"
        ).fetchall()
        albums = db.execute(
            "SELECT id, 'album' as type FROM albums ORDER BY created_at DESC"
        ).fetchall()
        results = [{"id": p["id"], "type": p["type"]} for p in photos]
        results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
        return results

    tag_list = [t.strip().lower() for t in tags.split() if t.strip()]
    if not tag_list:
        photos = db.execute(
            "SELECT id, 'photo' as type FROM photos WHERE album_id IS NULL ORDER BY uploaded_at DESC"
        ).fetchall()
        albums = db.execute(
            "SELECT id, 'album' as type FROM albums ORDER BY created_at DESC"
        ).fetchall()
        results = [{"id": p["id"], "type": p["type"]} for p in photos]
        results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
        return results

    # Find standalone photos that have ALL specified tags
    placeholders = ",".join("?" * len(tag_list))
    photos = db.execute(f"""
        SELECT p.id, 'photo' as type
        FROM photos p
        WHERE p.album_id IS NULL AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY p.uploaded_at DESC
    """, (*tag_list, len(tag_list))).fetchall()

    # Find albums where at least one photo has ALL specified tags
    albums = db.execute(f"""
        SELECT DISTINCT a.id, 'album' as type
        FROM albums a
        JOIN photos p ON p.album_id = a.id
        WHERE (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY a.created_at DESC
    """, (*tag_list, len(tag_list))).fetchall()

    results = [{"id": p["id"], "type": p["type"]} for p in photos]
    results.extend([{"id": a["id"], "type": a["type"]} for a in albums])
    return results


@router.post("/api/photos/batch-ai-tags")
def batch_generate_ai_tags(data: BatchAIInput, request: Request):
    """Generate AI tags for multiple photos and albums."""
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

    # Collect all photo IDs to process (individual + from albums)
    all_photo_ids = list(data.photo_ids)
    for album_id in data.album_ids:
        album_photos = db.execute(
            "SELECT id FROM photos WHERE album_id = ?", (album_id,)
        ).fetchall()
        all_photo_ids.extend([p["id"] for p in album_photos])

    processed = 0
    for photo_id in all_photo_ids:
        photo = db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if photo:
            # Select 3-6 random tags
            selected = random.sample(list(presets), min(random.randint(3, 6), len(presets)))

            # Clear existing tags and add new ones
            db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))

            for preset in selected:
                db.execute(
                    "INSERT INTO tags (photo_id, tag, category_id) VALUES (?, ?, ?)",
                    (photo_id, preset["name"], preset["category_id"])
                )

            # Mark as AI processed
            db.execute("UPDATE photos SET ai_processed = 1 WHERE id = ?", (photo_id,))
            processed += 1

    db.commit()
    return {"status": "ok", "processed": processed}


@router.get("/api/search")
def search_items_by_tags(tags: str = "", folder_id: str = None):
    """Search photos and albums by tags (space-separated) within a folder.
    
    Returns full item data for rendering in gallery.
    - Only searches within the specified folder
    - Album is included if at least one photo inside has ALL specified tags
    - Standalone photos (not in albums) are included directly
    - Photos inside albums are NOT shown separately - only the album
    """
    db = get_db()
    
    # Parse tags
    tag_list = [t.strip().lower() for t in tags.split() if t.strip()]
    
    if not tag_list or not folder_id:
        return {"items": [], "photos": [], "albums": []}
    
    placeholders = ",".join("?" * len(tag_list))
    
    # Find standalone photos (not in albums) in this folder with ALL specified tags
    standalone_photos = db.execute(f"""
        SELECT DISTINCT p.id, p.original_name, p.media_type, p.taken_at, p.uploaded_at,
               p.thumb_width, p.thumb_height, p.safe_id,
               (SELECT COUNT(*) FROM tags WHERE photo_id = p.id) as tag_count
        FROM photos p
        WHERE p.folder_id = ? AND p.album_id IS NULL AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY p.uploaded_at DESC
    """, (folder_id, *tag_list, len(tag_list))).fetchall()
    
    # Find albums in this folder where at least one photo has ALL specified tags
    albums = db.execute(f"""
        SELECT DISTINCT a.id, a.name, a.created_at, a.folder_id,
               (SELECT COUNT(*) FROM photos WHERE album_id = a.id) as photo_count,
               (SELECT id FROM photos WHERE album_id = a.id ORDER BY uploaded_at DESC LIMIT 1) as cover_photo_id,
               (SELECT thumb_width FROM photos WHERE album_id = a.id ORDER BY uploaded_at DESC LIMIT 1) as cover_thumb_width,
               (SELECT thumb_height FROM photos WHERE album_id = a.id ORDER BY uploaded_at DESC LIMIT 1) as cover_thumb_height,
               (SELECT safe_id FROM photos WHERE album_id = a.id ORDER BY uploaded_at DESC LIMIT 1) as safe_id
        FROM albums a
        JOIN photos p ON p.album_id = a.id
        WHERE a.folder_id = ? AND (
            SELECT COUNT(DISTINCT t.tag)
            FROM tags t
            WHERE t.photo_id = p.id AND LOWER(t.tag) IN ({placeholders})
        ) = ?
        ORDER BY a.created_at DESC
    """, (folder_id, *tag_list, len(tag_list))).fetchall()
    
    # Format standalone photos
    photo_results = []
    for p in standalone_photos:
        photo_results.append({
            "type": "photo",
            "id": p["id"],
            "original_name": p["original_name"],
            "media_type": p["media_type"] or "image",
            "taken_at": p["taken_at"],
            "uploaded_at": p["uploaded_at"],
            "thumb_width": p["thumb_width"],
            "thumb_height": p["thumb_height"],
            "safe_id": p["safe_id"]
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
            "cover_photo_id": a["cover_photo_id"],
            "cover_thumb_width": a["cover_thumb_width"],
            "cover_thumb_height": a["cover_thumb_height"],
            "safe_id": a["safe_id"]
        })
    
    # Combine items for gallery rendering: albums first, then standalone photos
    items = []
    items.extend(album_results)
    items.extend(photo_results)
    
    return {"items": items, "photos": photo_results, "albums": album_results}
