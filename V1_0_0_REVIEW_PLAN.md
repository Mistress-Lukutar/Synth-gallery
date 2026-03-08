# Review Plan for v1.0.0 Release

## Completed ✅

### 1. Python Legacy Code Fixes
- [x] Fix `api.py` - migrate from legacy `tags.photo_id` to new `item_tags` schema
- [x] Add missing `ai_processed` column to `item_media` table
- [x] Remove unused `api_key_valid` variables

### 2. JavaScript Cleanup
- [x] Remove 150+ console.log debug statements
- [x] Preserve console.error/console.warn for actual errors

## Remaining Tasks

### 3. API Endpoints Audit 🔍
**Goal:** Find and remove unused API endpoints

**Status:** ✅ AI tags feature removed (not used in UI, only manual tags)
- [x] Remove `/api/ai/*` endpoints (deleted api.py)
- [x] Remove `/api/items/{item_id}/ai-tags` endpoint from items.py
- [x] Remove `/api/items/batch-ai-tags` references from upload.js
- [x] Remove AI tags checkbox from base.html
- [x] Remove AI tags button from gallery.html
- [x] Remove ai_processed column from database schema
- [ ] Check remaining endpoints for usage

**Files to check:**
- [ ] `app/routes/api.py` - Check if AI endpoints are used
- [ ] `app/routes/tags.py` - Verify all endpoints have UI usage
- [ ] `app/routes/folders.py` - Check for orphaned endpoints
- [ ] `app/routes/gallery/items.py` - Verify copy/move endpoints
- [ ] `app/routes/safes.py` - Check safe management endpoints
- [ ] `app/routes/admin.py` - Verify maintenance endpoints

**Method:**
```bash
# Search for endpoint usage in JS files
grep -r "/api/" app/static/js/
grep -r "fetch.*api" app/static/js/
```

### 4. Templates Cleanup 🧹
**Goal:** Remove unused templates and blocks

**Files to audit:**
- [ ] `app/templates/base.html` - Check for unused blocks
- [ ] `app/templates/gallery.html` - Verify all sections used
- [ ] `app/templates/login.html` - Check legacy forms
- [ ] `app/templates/settings.html` - Verify all settings used
- [ ] `app/templates/admin_*.html` - Check admin templates

**Check for:**
- Commented out HTML blocks
- Legacy photo/album templates
- Unused modals
- Dead CSS classes

### 5. Configuration Cleanup ⚙️
**Goal:** Remove obsolete config variables

**Files:**
- [ ] `app/config.py` - Review all constants
- [ ] `.env` / environment variables documentation

**Check for:**
- Legacy storage configs
- Deprecated encryption settings
- Unused feature flags
- Old path configurations

### 6. Database Schema Cleanup 🗄️
**Goal:** Verify all tables/columns are used

**Check:**
- [ ] `photos` table - legacy, should be migrated
- [ ] `photo_keys` table - check if still used
- [ ] `folder_keys` table - verify usage
- [ ] `user_public_keys` table - check if E2E feature uses it
- [ ] `tags` table - verify v2.0 schema compatibility
- [ ] Indexes - check if all indexes are used

### 7. JavaScript Deep Cleanup 📜
**Goal:** Remove unused functions and modules

**Files to audit:**
- [ ] `app/static/js/crypto/` - Check all crypto modules usage
- [ ] `app/static/js/gallery-*.js` - Find dead functions
- [ ] `app/static/js/navigation.js` - Check SPA navigation

**Check for:**
- Functions never called
- Event listeners on non-existent elements
- Legacy photo vs item handling
- Commented code blocks

### 8. Dependencies Review 📦
**Goal:** Verify all requirements are used

**Files:**
- [ ] `requirements.txt` - Check each package
- [ ] `package.json` - If exists

**Check for:**
- Unused Python packages
- Development dependencies in production
- Version conflicts

### 9. Final Testing ✅
**Goal:** Ensure everything works after cleanup

**Tests:**
- [ ] Run full pytest suite
- [ ] Manual smoke test (upload, view, delete)
- [ ] Album operations test
- [ ] Safe (E2E) operations test
- [ ] Admin panel test
- [ ] Login/logout flow

## Progress Tracking

Update this section as tasks complete:

```markdown
- [x] Task 1
- [x] Task 2  
- [ ] Task 3 (in progress)
```

## Notes

### Deprecated Patterns Found
- `photo_id` references in API (fixed)
- `tags.photo_id` instead of `item_tags` (fixed)
- Console.log debugging (fixed)

### Potential Issues
- `api.py` endpoints may not be fully integrated with UI
- Some `photos` table references may remain
- Safe crypto has many complex functions - verify all used

## Tools for Analysis

```bash
# Python dead code
pip install vulture
vulture app/ --min-confidence 80

# JS dead code (requires Node)
npx eslint app/static/js/

# Find unused imports
pip install autoflake
autoflake --remove-unused-variables --remove-all-unused-imports -r app/

# Check database usage
grep -r "FROM photos" app/
grep -r "FROM items" app/
```
