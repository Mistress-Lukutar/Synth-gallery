"""AI Service API routes - protected by API key."""
from fastapi import APIRouter, HTTPException, Depends

from ..database import get_db
from ..dependencies import verify_api_key

router = APIRouter(prefix="/api/ai", tags=["ai-service"])


@router.get("/photos/untagged")
def get_untagged(_: bool = Depends(verify_api_key)):
    """List photos without tags (for AI service).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()
    # Query items without item_tags entries
    photos = db.execute("""
        SELECT i.id, im.filename
        FROM items i
        JOIN item_media im ON i.id = im.item_id
        LEFT JOIN item_tags it ON i.id = it.item_id
        WHERE i.type = 'media'
          AND it.id IS NULL
        ORDER BY i.uploaded_at ASC
        LIMIT 10
    """).fetchall()

    return [{"id": p["id"], "filename": p["filename"]} for p in photos]


@router.post("/photos/{photo_id}/tags")
def set_tags(photo_id: str, tags: list[str], _: bool = Depends(verify_api_key)):
    """Set tags for photo (called by AI service).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()

    # Check if item exists in items table
    item = db.execute("SELECT id FROM items WHERE id = ?", (photo_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404)

    # Delete old item_tags entries
    db.execute("DELETE FROM item_tags WHERE item_id = ?", (photo_id,))
    
    # Add new tags via item_tags junction table
    for tag_name in tags:
        tag_name = tag_name.lower().strip()
        # Find or create tag
        tag = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        if tag:
            tag_id = tag["id"]
        else:
            # Create new tag
            cursor = db.execute(
                "INSERT INTO tags (name, path, level, is_leaf) VALUES (?, ?, 0, 1)",
                (tag_name, tag_name)
            )
            tag_id = cursor.lastrowid
        
        # Link tag to item
        db.execute(
            "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (photo_id, tag_id)
        )

    # Mark as AI processed in item_media table
    db.execute("UPDATE item_media SET ai_processed = 1 WHERE item_id = ?", (photo_id,))
    db.commit()

    return {"status": "ok", "tags": tags}


@router.get("/stats")
def get_stats(_: bool = Depends(verify_api_key)):
    """Get tagging statistics (for AI service monitoring).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()

    # Count from items + item_media tables
    total_photos = db.execute("""
        SELECT COUNT(*) as count 
        FROM items i
        JOIN item_media im ON i.id = im.item_id
        WHERE i.type = 'media'
    """).fetchone()["count"]
    
    tagged_photos = db.execute("""
        SELECT COUNT(DISTINCT item_id) as count FROM item_tags
    """).fetchone()["count"]
    
    ai_processed = db.execute("""
        SELECT COUNT(*) as count 
        FROM item_media 
        WHERE ai_processed = 1
    """).fetchone()["count"]
    
    total_tags = db.execute("SELECT COUNT(*) as count FROM tags").fetchone()["count"]

    return {
        "total_photos": total_photos,
        "tagged_photos": tagged_photos,
        "untagged_photos": total_photos - tagged_photos,
        "ai_processed": ai_processed,
        "total_tags": total_tags
    }
