-- Phase 5B: Data Fix Scripts
-- Run these to fix validation issues before Phase 5C

-- ============================================================
-- 1. FIX ORPHANED ALBUM ITEMS
-- Delete album_items that reference non-existent items
-- ============================================================

-- First, see what will be deleted
SELECT ai.album_id, ai.item_id, a.name as album_name
FROM album_items ai
LEFT JOIN items i ON ai.item_id = i.id
LEFT JOIN albums a ON ai.album_id = a.id
WHERE i.id IS NULL;

-- Delete orphaned album_items
DELETE FROM album_items
WHERE item_id IN (
    SELECT ai.item_id
    FROM album_items ai
    LEFT JOIN items i ON ai.item_id = i.id
    WHERE i.id IS NULL
);

-- ============================================================
-- 2. FIX UNMIGRATED PHOTO
-- Migrate remaining photo to items architecture
-- ============================================================

-- Check the unmigrated photo
SELECT * FROM photos p
LEFT JOIN items i ON p.id = i.id
WHERE i.id IS NULL;

-- Migrate single photo to items
INSERT INTO items (
    id, type, folder_id, safe_id, user_id, uploaded_at,
    title, metadata, is_encrypted
)
SELECT 
    p.id,
    'media' as type,
    p.folder_id,
    p.safe_id,
    p.user_id,
    p.uploaded_at,
    p.original_name as title,
    NULL as metadata,
    p.is_encrypted
FROM photos p
LEFT JOIN items i ON p.id = i.id
WHERE i.id IS NULL;

-- Create corresponding item_media record
INSERT INTO item_media (
    item_id, media_type, filename, original_name, content_type,
    width, height, duration, thumb_width, thumb_height, taken_at, storage_mode
)
SELECT 
    p.id as item_id,
    CASE WHEN p.media_type = 'video' THEN 'video' ELSE 'image' END as media_type,
    p.id as filename,  -- Extension-less storage
    p.original_name,
    p.content_type,
    NULL as width,
    NULL as height,
    NULL as duration,
    p.thumb_width,
    p.thumb_height,
    p.taken_at,
    p.storage_mode
FROM photos p
LEFT JOIN items i ON p.id = i.id
WHERE i.id IS NOT NULL  -- Only for items we just created
AND NOT EXISTS (
    SELECT 1 FROM item_media im WHERE im.item_id = p.id
);

-- Migrate album membership if photo is in album
INSERT INTO album_items (album_id, item_id, position, added_at)
SELECT 
    p.album_id,
    p.id as item_id,
    p.position,
    p.uploaded_at as added_at
FROM photos p
WHERE p.album_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1 FROM album_items ai 
    WHERE ai.item_id = p.id AND ai.album_id = p.album_id
);

-- ============================================================
-- 3. VERIFICATION QUERIES
-- Run these to verify fixes
-- ============================================================

-- Check orphaned album_items (should return 0)
SELECT COUNT(*) as orphaned_count
FROM album_items ai
LEFT JOIN items i ON ai.item_id = i.id
WHERE i.id IS NULL;

-- Check unmigrated photos (should return 0)
SELECT COUNT(*) as unmigrated_count
FROM photos p
LEFT JOIN items i ON p.id = i.id
WHERE i.id IS NULL;

-- Check consistency
SELECT 
    (SELECT COUNT(*) FROM items) as items_count,
    (SELECT COUNT(*) FROM photos) as photos_count,
    (SELECT COUNT(*) FROM item_media) as item_media_count,
    (SELECT COUNT(*) FROM album_items) as album_items_count;
