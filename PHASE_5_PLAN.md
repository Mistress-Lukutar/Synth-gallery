# Phase 5 Migration Plan: Legacy Removal

## Overview
Complete migration from legacy PhotoRepository to polymorphic Item/Album architecture.

## Architecture

### Polymorphic Design (Issue #24/28)

```
┌─────────────────┐         ┌─────────────────────┐
│   items table   │         │  item_media table   │
├─────────────────┤         ├─────────────────────┤
│ id (PK)         │◄────────┤ item_id (PK, FK)    │
│ type            │         │ media_type          │
│ folder_id       │         │ filename            │
│ user_id         │         │ original_name       │
│ uploaded_at     │         │ content_type        │
│ title           │         │ width/height        │
│ is_encrypted    │         │ duration (video)    │
└─────────────────┘         │ thumb_width/height  │
                            │ taken_at (EXIF)     │
                            │ storage_mode        │
                            └─────────────────────┘
```

### Repository Responsibilities

| Repository | Table | Purpose |
|------------|-------|---------|
| **ItemRepository** | `items` | Universal content (photos, videos, notes, files) |
| **ItemMediaRepository** | `item_media` | Media-specific metadata (EXIF, thumbnails, dimensions) |
| **AlbumRepository** | `albums`, `album_items` | Album management and item ordering |

### ItemMediaRepository (7 methods)
- `create()` - Create media record on upload
- `get_by_item_id()` - Get media data by item ID
- `get_full_item()` - JOIN items + item_media
- `get_by_folder()` - Get all media in folder
- `update()` - Update fields (e.g., EXIF date)
- `update_thumbnail_dimensions()` - Update thumbnail size
- `delete()` - Delete media record

**Used in:** item_service.py, album_service.py, uploads.py, main.py, items.py, files.py

---

## Phase 5A: Code Migration (CRITICAL - Blocks Step 5)

### ✅ Priority 1: Core Services (COMPLETE)
- [x] `application/services/permission_service.py`
  - Replace `photo_repo.get_by_id()` with `item_repo.get_by_id()`
  - Replace `photo_repo.get_album()` with `album_repo.get_by_id()`
  - Update `can_access_photo()` → `can_access_item()` (with legacy alias)
  - Update `can_delete_photo()` → `can_delete_item()` (with legacy alias)
  - Update `can_access_album()` → use AlbumRepository
  - Update `can_delete_album()` → use AlbumRepository
  - Update `can_edit_album()` → use AlbumRepository

### ✅ Priority 2: File Operations (PARTIAL)
- [x] `application/services/folder_service.py`
  - Remove PhotoRepository dependency
  - Replace `get_standalone_photos()` with `get_standalone_items()`
  - Add `items` field with `photos` legacy alias

### 🔄 Priority 3: SafeFileService & Routes (IN PROGRESS)
- [ ] `application/services/safe_file_service.py`
  - Replace `photo_repo.get_by_id()` with `item_repo.get_by_id()`
  - Update E2E encryption metadata handling

- [ ] `routes/safe_files.py`
  - Replace PhotoRepository with ItemRepository
  - Update thumbnail dimension updates

### ⏳ Priority 4: Routes Cleanup
- [ ] `routes/gallery/albums.py`
  - Replace `photo_repo.get_album()` with `album_repo.get_by_id()`
  - Replace `photo_repo.get_album_photos()` with `album_repo.get_items()`
  - Replace `photo_repo.get_available_for_album()` with custom query

- [x] `routes/gallery/deps.py` (COMPLETE)
  - ✅ Updated get_permission_service
  - ✅ get_folder_service already correct

### ⏳ Priority 5: Legacy Upload (Optional - can deprecate later)
- [ ] `application/services/upload_service.py`
  - Mark as @deprecated
  - Or rewrite to use ItemRepository (large task)
  - NOTE: New uploads already use ItemService

### ⏳ Priority 6: Remove Legacy Code
- [ ] `application/services/photo_service.py`
  - Deprecate or remove (functionality moved to album_service.py)

- [ ] `infrastructure/repositories/photo_repository.py`
  - Mark all methods as @deprecated
  - Remove after all references gone

---

## Phase 5B: Data Validation & Cleanup

### Step 1: Data Consistency Check
```sql
-- Find orphaned album_items
SELECT ai.* FROM album_items ai
LEFT JOIN items i ON ai.item_id = i.id
WHERE i.id IS NULL;

-- Check for unmigrated photos
SELECT COUNT(*) FROM photos p
LEFT JOIN items i ON p.id = i.id
WHERE i.id IS NULL;

-- Verify album_items matches photos.album_id
SELECT p.id, p.album_id FROM photos p
WHERE p.album_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1 FROM album_items ai 
    WHERE ai.item_id = p.id AND ai.album_id = p.album_id
);
```

### Step 2: Fix Orphaned Data
- [ ] Delete orphaned album_items (26 records found)
- [ ] Migrate any remaining photos → items (if found)
- [ ] Validate album cover_item_id references

### Step 3: Pre-Cleanup Verification
- [ ] All photos have corresponding items
- [ ] All album_ids in photos migrated to album_items
- [ ] No code references PhotoRepository
- [ ] All tests pass

---

## Phase 5C: Database Cleanup (Step 5)
- [ ] Drop column `photos.album_id`
- [ ] Drop table `photos` (after full migration verified)
- [ ] Remove PhotoRepository class

---

## Progress Summary

**Started:** 2026-03-02
**Last Updated:** 2026-03-02
**Status:** Phase 5A in progress (~60% complete)

### Commits Created:
1. `78a2606` - refactor(5A): migrate PermissionService
2. `e81dcc7` - refactor(5A): migrate FolderService

### Tests Status:
- ✅ 32 passed, 1 skipped
- ✅ All core functionality working

### Next Steps:
1. Complete Priority 3 (safe_file_service.py, safe_files.py routes)
2. Update albums.py routes
3. Phase 5B data validation
4. Phase 5C database cleanup
