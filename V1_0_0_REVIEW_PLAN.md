# Review Plan for v1.0.0 Release

## Completed ✅

### 1. Python Legacy Code Fixes
- [x] Fix `api.py` - migrate from legacy `tags.photo_id` to new `item_tags` schema
- [x] Add missing `ai_processed` column to `item_media` table (then removed)
- [x] Remove unused `api_key_valid` variables
- [x] Remove unused `api.py` router from main.py

### 2. JavaScript Cleanup
- [x] Remove 150+ console.log debug statements
- [x] Preserve console.error/console.warn for actual errors

### 3. API Endpoints Audit 🔍
- [x] Remove `/api/ai/*` endpoints (deleted api.py)
- [x] Remove `/api/items/{item_id}/ai-tags` endpoint from items.py
- [x] Remove `/api/items/batch-ai-tags` references from upload.js
- [x] Remove AI tags checkbox from base.html
- [x] Remove AI tags button from gallery.html
- [x] Remove `ai_processed` column from database schema
- [x] Remove `item_keys` table (was unused)
- [x] Remove encryption key copying logic

### 4. Templates Cleanup 🧹
- [x] Verified all templates are in use
- [x] No TODO/FIXME comments in templates
- [x] UI terminology uses "photo" appropriately

### 5. Configuration Cleanup ⚙️
- [x] Remove `AI_API_KEY` from config.py
- [x] Remove `verify_api_key` dependency function
- [x] Clean up dependencies.py

### 6. Database Schema Cleanup 🗄️
- [x] Remove `item_keys` table from init_db
- [x] `photos` table not in init_db (legacy, empty)
- [x] `photo_keys` table has old data but not used in code
- [x] `folder_keys` and `user_public_keys` used for E2E

## Remaining Tasks

### 7. Dependencies Review 📦
**Goal:** Verify all requirements are used

**Status:** In Progress
- [ ] `requirements.txt` - Check each package
- [ ] Check for unused imports in Python files

### 8. Final Testing ✅
**Goal:** Ensure everything works after cleanup

**Tests:**
- [ ] Run full pytest suite
- [ ] Manual smoke test (upload, view, delete)
- [ ] Album operations test
- [ ] Safe (E2E) operations test
- [ ] Admin panel test
- [ ] Login/logout flow

## Notes

### Changes Made
- Deleted AI service entirely (api.py, config, UI elements)
- Removed 150+ debug console.log statements
- Fixed album copy to use correct column names
- Removed unused `item_keys` table
- Fixed legacy schema references (`tags.photo_id` → `item_tags`)

### Commits
1. `527402e` - fix: album copy and SPA gallery refresh
2. `b3434de` - refactor: cleanup for v1.0.0 release
3. `1b9d7b9` - refactor: remove unused AI tags feature
4. `e21ba3a` - refactor: remove AI service configuration
5. `65fe90f` - fix: remove api_router import from main.py
6. `d79300e` - refactor: remove unused item_keys table
