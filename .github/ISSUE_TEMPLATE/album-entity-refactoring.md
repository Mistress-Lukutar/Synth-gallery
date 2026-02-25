---
name: Architecture Improvement
description: Album entity is overly complex and mixed with PhotoRepository
title: "[ARCH] Refactor Album entity: extract AlbumRepository and simplify logic"
labels: ["architecture", "refactoring", "technical-debt"]
---

## Architecture Improvement Proposal

The Album entity has grown beyond a simple "container for photos" and now has excessive responsibilities spread across multiple repositories.

### Priority
🟡 High (significant tech debt)

### Category
Code Organization

### Current Problem

The Album entity currently has **~20 methods** scattered across `PhotoRepository` and mixed concerns:

**Database Schema Issues:**
- Double ownership: photos have both `folder_id` AND `album_id` with `position`
- Complex foreign keys: `cover_photo_id` references photos
- Album deletion logic has two modes (keep photos vs delete photos)

**Repository Bloat (PhotoRepository):**
```python
# Album-related methods in PhotoRepository (~20 methods):
- get_album, create_album, delete_album, delete_album_with_photos
- add_to_album, remove_from_album, add_photo_to_album, remove_photo_from_album  
- reorder_album, reorder_in_album
- set_album_cover, move_album_to_folder
- get_album_photos, get_photos_in_album, get_by_album, get_available_for_album
- update_album_name, get_album_by_id, count_by_album
```

**Frontend Complexity (gallery-albums.js - 520 lines):**
- Album opens in lightbox but also has separate editor panel
- Drag-drop reordering with cover selection
- Separate navigation context inside lightbox
- Album "expansion" logic in lightbox navigation

**API Endpoints (albums.py - 174 lines):**
- 8 endpoints for CRUD + cover + reorder + move + available photos

### Proposed Solution

1. **Extract AlbumRepository** 
   - Move all album-related DB operations from PhotoRepository
   - Single responsibility: PhotoRepository → photos, AlbumRepository → albums

2. **Simplify Album Logic**
   - Remove manual `position` field → use auto-sort by date (taken_at/uploaded_at)
   - Remove `cover_photo_id` → use first photo as cover (already have fallback logic)
   - Consider removing `move_album` operation (move photos individually instead)

3. **Clarify Ownership Model**
   - Decision needed: Should album be a "view" (virtual collection) or "container" (physical ownership)?
   - Current: Hybrid - photos stay in folder, but album controls display order

### Files Affected

- `app/infrastructure/repositories/photo_repository.py` (refactor - remove album methods)
- `app/infrastructure/repositories/album_repository.py` (new - extract album logic)
- `app/application/services/photo_service.py` (update imports)
- `app/routes/gallery/albums.py` (simplify if removing reorder/move)
- `app/static/js/gallery-albums.js` (simplify if removing drag-drop reorder)
- `app/static/js/gallery-lightbox.js` (simplify album expansion logic)
- `app/database.py` (migration: remove position column if simplifying)

### Risks & Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing album reordering | Maintain position column as deprecated, use only for legacy albums |
| Loss of custom cover functionality | Ensure first-photo-as-cover works well in UI |
| Migration complexity | Write DB migration to populate taken_at from position order if needed |
| Frontend refactoring | Do in phases: backend first, then gradual frontend simplification |

### Implementation Checklist

- [ ] Create AlbumRepository with extracted methods
- [ ] Update PhotoRepository to remove album methods  
- [ ] Write tests for AlbumRepository
- [ ] Evaluate: keep or remove `position` field?
- [ ] Evaluate: keep or remove `cover_photo_id`?
- [ ] Update ROADMAP.md and documentation
- [ ] Performance benchmark album loading

### Related Code Analysis

**Current album flow:**
1. User clicks album in gallery → `handleAlbumClick()` 
2. `openAlbum()` fetches `/api/albums/{id}` with photos ordered by `position`
3. Lightbox opens with album context → `setAlbumContext()`
4. Navigation uses `expandAlbumInLightboxNav()` to merge with gallery order

**Simplified flow (proposed):**
1. User clicks album → `openAlbum()`
2. Fetch album photos ordered by `taken_at` or `uploaded_at` (same as gallery sort)
3. Lightbox opens with photo array directly
4. No "expansion" logic needed - consistent ordering with gallery view

### Additional Context

See analysis in commit `1f02403` (chronological navigation fix) - this revealed that visual masonry order was breaking navigation consistency, suggesting album ordering should follow the same rules as gallery sorting.

**Estimated effort:** 2-3 days for backend, 1-2 days for frontend cleanup
**Impact:** Medium - improves maintainability, reduces code duplication
