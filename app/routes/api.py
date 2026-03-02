"""AI Service API routes - protected by API key."""
from fastapi import APIRouter, HTTPException, Depends

from ..database import get_db
from ..dependencies import verify_api_key

router = APIRouter(prefix="/api/ai", tags=["ai-service"])


@router.get("/photos/untagged")
def get_untagged(api_key_valid: bool = Depends(verify_api_key)):
    """List photos without tags (for AI service).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()
    # Phase 5: Query items + item_media instead of photos
    photos = db.execute("""
        SELECT i.id, im.filename
        FROM items i
        JOIN item_media im ON i.id = im.item_id
        LEFT JOIN tags t ON i.id = t.photo_id
        WHERE i.type = 'media'
          AND t.id IS NULL
        ORDER BY im.uploaded_at ASC
        LIMIT 10
    """).fetchall()

    return [{"id": p["id"], "filename": p["filename"]} for p in photos]


@router.post("/photos/{photo_id}/tags")
def set_tags(photo_id: str, tags: list[str], api_key_valid: bool = Depends(verify_api_key)):
    """Set tags for photo (called by AI service).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()

    # Phase 5: Check if item exists in items table
    item = db.execute("SELECT id FROM items WHERE id = ?", (photo_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404)

    # Delete old tags and add new ones
    db.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))
    for tag in tags:
        db.execute(
            "INSERT INTO tags (photo_id, tag) VALUES (?, ?)",
            (photo_id, tag.lower().strip())
        )

    # Phase 5: Mark as AI processed in item_media table
    db.execute("UPDATE item_media SET ai_processed = 1 WHERE item_id = ?", (photo_id,))
    db.commit()

    return {"status": "ok", "tags": tags}


@router.get("/stats")
def get_stats(api_key_valid: bool = Depends(verify_api_key)):
    """Get tagging statistics (for AI service monitoring).

    Requires API key authentication via X-API-Key header.
    """
    db = get_db()

    # Phase 5: Count from items + item_media tables
    total_photos = db.execute("""
        SELECT COUNT(*) as count 
        FROM items i
        JOIN item_media im ON i.id = im.item_id
        WHERE i.type = 'media'
    """).fetchone()["count"]
    
    tagged_photos = db.execute("""
        SELECT COUNT(DISTINCT photo_id) as count FROM tags
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
