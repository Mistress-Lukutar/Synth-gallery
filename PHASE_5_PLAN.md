# Phase 5 Migration Plan: Legacy Removal

## Overview
Complete migration from legacy PhotoRepository to polymorphic Item/Album architecture.

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

### 🔄 Priority 2: File Operations
- [ ] `application/services/folder_service.py`
  - Replace `photo_repo.get_by_folder()` with `item_repo.get_by_folder()`
  - Update `_delete_photo_files()` → `_delete_item_files()`

- [ ] `application/services/safe_file_service.py`
  - Replace `photo_repo.get_by_id()` with `item_repo.get_by_id()`
  - Update E2E encryption metadata handling

- [ ] `routes/safe_files.py`
  - Replace PhotoRepository with ItemRepository
  - Update thumbnail dimension updates

### ⏳ Priority 3: Legacy Upload (Optional - can deprecate later)
- [ ] `application/services/upload_service.py`
  - Mark as @deprecated
  - Or rewrite to use ItemRepository (large task)
  - NOTE: New uploads already use ItemService

### ⏳ Priority 4: Routes Cleanup
- [ ] `routes/gallery/albums.py`
  - Replace `photo_repo.get_album()` with `album_repo.get_by_id()`
  - Replace `photo_repo.get_album_photos()` with `album_repo.get_items()`
  - Replace `photo_repo.get_available_for_album()` with custom query

- [ ] `routes/gallery/deps.py` (partially done)
  - ✅ Updated get_permission_service
  - ⏳ Other services still use PhotoRepository

### ⏳ Priority 5: Remove Legacy Code
- [ ] `application/services/photo_service.py`
  - Deprecate or remove (functionality moved to album_service.py)

- [ ] `infrastructure/repositories/photo_repository.py`
  - Mark all methods as @deprecated
  - Remove after all references gone

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

## Phase 5C: Database Cleanup (Step 5)
- [ ] Drop column `photos.album_id`
- [ ] Drop table `photos` (after full migration verified)
- [ ] Remove PhotoRepository class

## Progress Tracking
Started: 2026-03-02
Updated: 2026-03-02

### Completed:
- ✅ Phase 5A Priority 1: PermissionService migrated
- ✅ All tests passing (32 passed, 1 skipped)

### Next:
- 🔄 Phase 5A Priority 2: folder_service.py
