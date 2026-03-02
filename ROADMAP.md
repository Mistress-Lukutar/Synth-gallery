# Synth Gallery Architecture Roadmap

This document tracks planned architectural improvements, refactoring goals, and technical debt resolution.

> **Last Updated:** 2026-02-18  
> **Status:** v1.0 Release Preparation  
> **Priority Legend:** 🔴 Critical | 🟡 High | 🟢 Medium | 🔵 Low

---

## Quick Overview

| Priority    | Issue                                                     | Solution                        | Effort | Status         |
|-------------|-----------------------------------------------------------|---------------------------------|--------|----------------|
| 🔴 Critical | [#14](https://github.com/Nate-go/Synth-Gallery/issues/14) | God Module - Repository Pattern | Large  | ✅ **DONE**     |
| 🔴 Critical | [#15](https://github.com/Nate-go/Synth-Gallery/issues/15) | ~~Async Database (aiosqlite)~~  | Medium | ❌ **REVERTED** |
| 🟡 High     | [#16](https://github.com/Nate-go/Synth-Gallery/issues/16) | Business Logic Extraction       | Medium | ✅ **DONE**     |
| 🟡 High     | [#22](https://github.com/Nate-go/Synth-Gallery/issues/22) | Album Entity + File Storage Refactoring | Medium | ✅ **DONE**     |
| 🔴 Critical | [#23](https://github.com/Nate-go/Synth-Gallery/issues/23) | Unified File Access Service     | Large  | ✅ **DONE** |
| 🔴 Critical | [#24](https://github.com/Nate-go/Synth-Gallery/issues/24) | Polymorphic Items & Albums v1.0 | Large  | ✅ **Phases 1-4 DONE** |
| 🔴 Critical | [#28](https://github.com/Nate-go/Synth-Gallery/issues/28) | Phase 5: Complete Legacy Removal | Large  | 🔫 **IN PROGRESS** |
| 🟡 High     | [#17](https://github.com/Nate-go/Synth-Gallery/issues/17) | SQLAlchemy Core / Alembic       | Large  | 🔲 Planned     |
| 🟡 High     | [#18](https://github.com/Nate-go/Synth-Gallery/issues/18) | Redis / Encrypted Sessions      | Medium | ✅ **DONE**     |
| 🟢 Medium   | [#19](https://github.com/Nate-go/Synth-Gallery/issues/19) | Storage Interface (S3/local)    | Medium | ✅ **DONE**     |
| 🟢 Medium   | [#20](https://github.com/Nate-go/Synth-Gallery/issues/20) | Secure Cookie Settings          | Small  | ✅ **DONE**     |
| 🔵 Low      | [#21](https://github.com/Nate-go/Synth-Gallery/issues/21) | Request Validation Models       | Small  | ✅ **DONE**     |

---

## Completed Issues

### Issue #14: God Module Refactoring 🔴 ✅

**Status:** **COMPLETED** - 2026-02-18

**Problem:**  
The `app/database.py` file had grown to 2100+ lines, containing schema definitions, migrations, CRUD operations for all entities, business logic, and encryption key management.

**Solution Implemented:**
```
app/
└── infrastructure/
    └── repositories/
        ├── base.py            # Repository base class
        ├── user_repository.py      ✅ UserRepository
        ├── session_repository.py   ✅ SessionRepository  
        ├── folder_repository.py    ✅ FolderRepository
        ├── permission_repository.py ✅ PermissionRepository
        ├── photo_repository.py     ✅ PhotoRepository
        └── safe_repository.py      ✅ SafeRepository
```

**Results:**
- ✅ 6 repositories extracted
- ✅ database.py reduced from 2100+ to ~900 lines (-57%)
- ✅ All existing tests pass (38/39)
- ✅ Backward compatibility maintained (proxy functions)
- ✅ No breaking changes

**Migration Guide:**
```python
# Old way (still works via proxies):
from app.database import create_user, get_user_by_id
user_id = create_user(...)

# New way (recommended):
from app.infrastructure.repositories import UserRepository
from app.database import get_db
repo = UserRepository(get_db())
user_id = repo.create(...)
```

---

### Issue #15: Async Database Layer 🔴 ❌

**Status:** **REVERTED** - 2026-02-18

**Original Problem:**  
FastAPI is an async framework, but database operations use synchronous SQLite (`sqlite3` module), potentially blocking the event loop.

**Original Solution (Implemented & Reverted):**
- ✅ Added `aiosqlite` for async SQLite operations
- ✅ Created `app/infrastructure/database/` with async connection pool
- ✅ Added `AsyncRepository` base class with async execute/fetch methods
- ✅ Created async versions of all 6 repositories
- ✅ Added `get_async_db()` FastAPI dependency

**Why Reverted:**
1. **No production usage** - All route handlers continued using sync repositories
2. **Code complexity** - Maintaining both sync and async versions doubled codebase
3. **No measurable benefit** - SQLite is file-based; async doesn't improve I/O
4. **Issue #17 (SQLAlchemy)** - Planned migration to SQLAlchemy Core will provide better async ORM
5. **YAGNI principle** - Added complexity without actual need

**Lessons Learned:**
- Don't add async "just because FastAPI supports it"
- For file-based databases (SQLite), async provides minimal benefit
- Wait for actual performance bottlenecks before optimizing
- SQLAlchemy 2.0+ provides better async abstraction than raw aiosqlite

**Current State:**
- ❌ `app/infrastructure/database/` removed
- ❌ All `Async*Repository` classes removed
- ✅ Only sync repositories remain (cleaner codebase)
- ✅ Routes use `create_connection()` with explicit close

---

### Issue #16: Service Layer Extraction 🟡 ✅

**Status:** **COMPLETED** - 2026-02-18

**Problem:**  
Business logic embedded directly in FastAPI route handlers:
- `app/routes/gallery.py` (1400+ lines)
- Upload logic duplicated between single/bulk/album
- HTTP concerns mixed with business rules
- No separation between web layer and domain logic

**Solution Implemented:**
```
app/application/
├── __init__.py
└── services/
    ├── __init__.py
    ├── auth_service.py          ✅ AuthService
    ├── folder_service.py        ✅ FolderService
    ├── permission_service.py    ✅ PermissionService
    ├── photo_service.py         ✅ PhotoService
    ├── safe_service.py          ✅ SafeService
    ├── safe_file_service.py     ✅ SafeFileService
    ├── upload_service.py        ✅ UploadService
    ├── user_settings_service.py ✅ UserSettingsService
    └── envelope_service.py      ✅ EnvelopeService
```

**Routes Migrated:**
- ✅ `auth.py` - AuthService + UserSettingsService
- ✅ `admin.py` - UserRepository
- ✅ `folders.py` - FolderService + PermissionService + UserSettingsService
- ✅ `gallery.py` - PhotoService + UploadService + PermissionService
- ✅ `safes.py` - SafeService + WebAuthnRepository
- ✅ `safe_files.py` - SafeFileService
- ✅ `webauthn.py` - WebAuthnRepository + SessionRepository
- ✅ `envelope.py` - EnvelopeService
- ✅ `middleware.py` - SessionRepository

**Results:**
- ✅ 9 application services created
- ✅ 7 repositories implemented
- ✅ `database.py` reduced from 2282 to ~450 lines (-80%)
- ✅ All routes use `create_connection()` pattern
- ✅ 128 tests passing (100% pass rate)
- ✅ No deprecated database functions in production code
- ✅ Clean separation: Routes → Services → Repositories → DB

**Migration Example:**
```python
# Before (in route):
folder = get_folder(folder_id)
if folder["user_id"] != user["id"]:
    raise HTTPException(403, "Access denied")
update_folder(folder_id, data.name)

# After (using service):
service = get_folder_service()
folder = service.update_folder(folder_id, data.name, user["id"])
```

**Completed:**
- [x] Create application services (Folder, Permission, Safe, Upload, Photo)
- [x] Refactor `routes/folders.py` to use FolderService
- [x] Refactor gallery.py `/upload` endpoint to use UploadService
- [x] Refactor gallery.py `/upload-album` endpoint to use UploadService
- [x] Refactor gallery.py `/upload-bulk` endpoint to use UploadService
- [x] Refactor gallery.py `/api/photos/batch-delete` endpoint to use UploadService
- [x] Refactor gallery.py move endpoints to use PhotoService:
  - `/api/photos/{id}/move`
  - `/api/albums/{id}/move`
  - `/api/items/move`
- [x] Refactor gallery.py album management endpoints to use PhotoService:
  - `/api/albums/{id}/photos` (add)
  - `/api/albums/{id}/photos` (remove)
  - `/api/albums/{id}/reorder`
  - `/api/albums/{id}/cover`
- [x] Fix PhotoRepository integration with UploadService
- [x] Fix Python 3.12 datetime adapter deprecation warning
- [x] Add comprehensive service layer unit tests (30 tests total)

**Next Steps:**
- [x] Refactor safe routes to use SafeService
- [x] Extract remaining business logic from envelope.py

---

### Issue #19: Storage Abstraction Layer 🟢 ✅

**Status:** **COMPLETED** - 2026-02-26

**Problem:**  
Direct filesystem operations throughout codebase:
```python
with open(UPLOADS_DIR / filename, "wb") as f:
    f.write(content)
```
Cannot easily switch to S3, MinIO, or network storage.

**Solution Implemented:**
```
app/infrastructure/storage/
├── base.py                  # StorageInterface (abstract)
├── local_storage.py         # LocalStorage - filesystem backend
├── s3_storage.py           # S3Storage - AWS/MinIO/DigitalOcean
├── encrypted_storage.py    # EncryptedStorage - E2E wrapper
└── factory.py              # get_storage() - backend selection
```

**Integration Points:**
- ✅ `UploadService` - uses `storage.upload()` / `storage.delete()`
- ✅ `files.py` routes - uses `storage.download()` / `storage.get_url()`
- ✅ `backup.py` - uses `storage.list_files()` for S3 backups

**Configuration:**
```bash
# Local storage (default)
STORAGE_BACKEND=local

# S3/MinIO
STORAGE_BACKEND=s3
S3_BUCKET=mybucket
S3_REGION=us-east-1
S3_ENDPOINT=https://minio.example.com  # Optional
S3_ACCESS_KEY=xxx
S3_SECRET_KEY=xxx
```

**Tests:**
- 31 unit tests for LocalStorage
- 9 unit tests for EncryptedStorage
- 12 integration tests for backup with storage
- Total: 52 new tests, all passing

**Migration Notes:**
- Existing files remain in place when switching backends
- New files go to configured backend
- Backups work with any backend (downloads from S3 if needed)

---

### Issue #18: DEK Cache Persistence (DB Sessions) 🟡 ✅

**Status:** **COMPLETED** - 2026-03-02

**Problem:**  
Current DEK (Data Encryption Key) cache is in-memory Python dict:
```python
dek_cache: dict[int, tuple[bytes, datetime]] = {}
```

This causes several issues:
- ❌ Lost on server restart (all users must re-login)
- ❌ Doesn't work with multiple workers (Gunicorn)
- ❌ No cross-process invalidation
- ❌ Cannot do remote logout

**Solution Implemented (Option B - DB Sessions):**

1. **Database Migration** (`app/database.py`):
   ```sql
   ALTER TABLE sessions ADD COLUMN encrypted_dek BLOB
   ```

2. **SessionDEKService** (`app/infrastructure/services/session_dek.py`):
   - Encrypts DEK with AES-256-GCM using session-derived key
   - Session ID acts as encryption key (via PBKDF2)
   - Security: even with DB access, DEK cannot be decrypted without session_id

3. **Updated SessionRepository** (`app/infrastructure/repositories/session_repository.py`):
   - `create()` - accepts optional `encrypted_dek` parameter
   - `set_encrypted_dek()` - store encrypted DEK for session
   - `get_encrypted_dek()` - retrieve encrypted DEK

4. **AuthService Integration** (`app/application/services/auth_service.py`):
   - `store_dek_in_session()` - encrypt and store DEK
   - `get_dek_from_session()` - retrieve and decrypt DEK
   - `decrypt_and_cache_dek()` - now accepts `session_id` for persistence

5. **Middleware DEK Restoration** (`app/middleware.py`):
   ```python
   # On each request: if DEK not in memory, try to restore from session
   if dek_cache.get(user_id) is None and session.get("encrypted_dek"):
       dek = SessionDEKService.decrypt_dek(session["encrypted_dek"], session_id)
       dek_cache.set(user_id, dek)
   ```

**Security Model:**
```
Login: Password → KEK → Decrypt DEK → Encrypt with session_id → Store in DB
Request: Cookie (session_id) → Get from DB → Decrypt with session_id → Cache in memory
Logout: Delete session from DB → DEK no longer accessible
```

**Benefits:**
- ✅ DEK persists across server restarts
- ✅ Works with multiple workers (Gunicorn)
- ✅ Remote logout possible (delete session)
- ✅ No plaintext DEK in database
- ✅ Session ID never leaves server (HTTP-only cookie)

**Files Changed:**
- ✅ `app/database.py` - migration for `encrypted_dek` column
- ✅ `app/infrastructure/services/session_dek.py` - NEW service
- ✅ `app/infrastructure/repositories/session_repository.py` - DEK methods
- ✅ `app/application/services/auth_service.py` - session DEK integration
- ✅ `app/middleware.py` - DEK restoration from session
- ✅ `app/routes/auth.py` - store DEK on login
- ✅ `app/routes/webauthn.py` - store DEK on WebAuthN login
- ✅ `tests/unit/test_session_dek_service.py` - NEW unit tests
- ✅ `tests/unit/test_session_repository_dek.py` - NEW unit tests

---

### Issue #20: Secure Cookie Settings 🟢 ✅

**Status:** **COMPLETED** - 2026-03-02

**Problem:**  
CSRF and session cookies used insecure settings:
```python
response.set_cookie(
    key=CSRF_COOKIE_NAME,
    secure=False,      # Sent over HTTP
    httponly=False,    # Accessible via JavaScript
)
```

**Solution Implemented:**

1. **Added environment-based configuration** (`app/config.py`):
   ```python
   SYNTH_ENV = os.environ.get("SYNTH_ENV", "development")
   COOKIE_SECURE = SYNTH_ENV == "production"
   ```

2. **Updated all cookie settings:**
   | Cookie | Before | After |
   |--------|--------|-------|
   | CSRF | `secure=False` | `secure=COOKIE_SECURE` |
   | CSRF | `httponly=False` | `httponly=False` (JS needs it) |
   | Session | `secure` not set | `secure=COOKIE_SECURE` |
   | Session | `httponly=True` | `httponly=True` |

3. **Files changed:**
   - ✅ `app/config.py` - Added `SYNTH_ENV` and `COOKIE_SECURE`
   - ✅ `app/middleware.py` - CSRF cookie uses `COOKIE_SECURE`
   - ✅ `app/routes/auth.py` - Session cookie uses `COOKIE_SECURE`
   - ✅ `app/routes/webauthn.py` - Session cookie uses `COOKIE_SECURE`

**Usage:**
```bash
# Development (default) - cookies sent over HTTP
# (no change needed)

# Production - cookies require HTTPS
set SYNTH_ENV=production  # Windows
export SYNTH_ENV=production  # Linux/macOS
```

**Security Benefits:**
- ✅ CSRF token cannot be stolen via MITM (HTTPS-only in production)
- ✅ Session cookie is HTTP-only (XSS protection)
- ✅ Session cookie requires HTTPS in production

---

### Issue #21: Pydantic Request Validation 🔵 ✅

**Status:** **COMPLETED** - 2026-02-25

**Problem:**  
Form data and JSON endpoints not validated:
```python
@router.post("/upload")
async def upload(folder_id: str = Form(None)):  # No validation!

# Manual validation everywhere:
if not folder_id:
    raise HTTPException(status_code=400, detail="folder_id required")
```

**Solution Implemented:**
1. **Form endpoints** - Changed `Form(None)` to `Form(...)` for required fields:
   - `uploads.py`: `folder_id`, `paths` now use `Form(...)`
   - FastAPI automatically returns 422 if fields missing

2. **JSON endpoints** - Created Pydantic models:
   - `ThumbnailDimensionsInput` (photos.py)
   - `AlbumMoveInput` (albums.py) 
   - `SortPreferenceInput` (main.py)

3. **Removed manual parsing:**
   - Deleted `json.loads(request.body())` patterns
   - Deleted inline `class MoveInput(BaseModel)` definitions

**Files Changed:**
- ✅ `auth.py` - `LoginRequest` model
- ✅ `uploads.py` - `Form(...)` validation
- ✅ `photos.py` - `ThumbnailDimensionsInput`
- ✅ `albums.py` - `AlbumMoveInput`
- ✅ `main.py` - `SortPreferenceInput`

**Results:**
- ~33 lines of manual validation code removed
- Automatic 422 responses with detailed error messages
- Type safety and IDE autocomplete support
- Swagger UI documentation improved

---

### Issue #22: Album Entity Refactoring 🟡

**Status:** **PLANNED**

**Problem:**  
The Album entity has grown beyond a simple "container for photos" with excessive responsibilities:

1. **Repository Bloat**: PhotoRepository contains ~20 album-related methods:
   - `get_album`, `create_album`, `delete_album`, `delete_album_with_photos`
   - `add_to_album`, `remove_from_album`, `reorder_album`, `set_album_cover`
   - `move_album_to_folder`, `get_album_photos`, etc.

2. **Complex Schema**: Double ownership model creates confusion:
   - Photos have `folder_id` (physical location) AND `album_id` + `position` (display order)
   - `cover_photo_id` adds another relationship
   - Album deletion has two modes (keep photos vs delete)

3. **Frontend Complexity**: `gallery-albums.js` (520 lines) handles:
   - Lightbox integration with "album expansion" logic
   - Drag-drop reordering with cover selection
   - Separate album editor panel

**Proposed Solution:**
1. **Extract AlbumRepository** - Move all album DB operations from PhotoRepository
2. **Simplify Ordering** - Remove manual `position` field, use date-based sorting (consistent with gallery)
3. **Evaluate Cover** - Consider removing `cover_photo_id`, use first photo as cover (fallback already exists)
4. **Clarify Ownership** - Decision: virtual collection vs physical container?

**Files Affected:**
- `app/infrastructure/repositories/photo_repository.py` (remove album methods)
- `app/infrastructure/repositories/album_repository.py` (new)
- `app/routes/gallery/albums.py` (simplify)
- `app/static/js/gallery-albums.js` (simplify)
- `app/database.py` (potential migration)

**Related Issue:** Lightbox navigation fix (commit `1f02403`) revealed that masonry visual order breaks navigation - suggests album ordering should follow same chronological rules as gallery.

---

### Issue #22 Part B: File Extension Cleanup 🟡 ✅

**Status:** **COMPLETED** - 2026-02-27

**Problem:**
Files stored with extensions caused complexity and security issues:
- Extension parsing in paths: `f"{photo_id}.{ext}"`
- MIME detection from extension (unreliable)
- Security: PHP scripts disguised as .jpg
- Inconsistent: uploads have ext, thumbnails forced to .jpg

**Solution Implemented:**
```
Before: uploads/abc123.jpg + thumbnails/abc123.jpg
After:  uploads/abc123       + thumbnails/abc123
```

**Changes Made:**

1. **Database:**
   - Added `content_type` column to photos table
   - Migration in `init_db()` for existing databases

2. **UploadService:**
   - Filename = photo_id (no extension)
   - Thumbnail = photo_id (no .jpg)
   - `content_type` from UploadFile stored in DB
   - Added `_validate_file_content()` for magic bytes verification

3. **Frontend:**
   - All JS files updated to use extension-less URLs
   - `/thumbnails/${photoId}` instead of `${photoId}.jpg`
   - `/uploads/${photoId}` instead of `${photoId}${ext}`

4. **Security:**
   - Magic bytes validation prevents spoofing
   - PHP/HTML/EXE disguised as images rejected
   - 12 security tests added, all passing

**Benefits Achieved:**
| Aspect | Before | After |
|--------|--------|-------|
| Storage key | `f"{id}.{ext}"` | `id` (simpler) |
| Thumbnail key | `f"{id}.jpg"` | `id` (consistent) |
| MIME detection | From extension | From DB (reliable) |
| Security | Extension spoofing risk | Magic bytes validation |
| Code complexity | High (path parsing) | Low (direct ID) |

**Migration:**
- New uploads: extension-less
- Existing: use `scripts/migrate_extensions.py`
- Thumbnails: regenerate via admin panel

**Tests:**
- 12 upload tests passing
- 12 security/spoofing tests passing
- Total: 24 new tests

**Files Modified:**
- `app/database.py` - content_type column
- `app/infrastructure/repositories/photo_repository.py` - create() updated
- `app/infrastructure/services/thumbnail.py` - extension-less paths
- `app/application/services/upload_service.py` - no ext, magic validation
- `app/application/services/safe_file_service.py` - no ext
- `app/routes/folders.py` - thumbnail paths
- `app/routes/gallery/photos.py` - thumbnail paths
- `app/routes/gallery/files.py` - MIME from DB
- `app/static/js/*` - extension-less URLs
- `scripts/migrate_extensions.py` - migration script

---

### Issue #17: Database Abstraction & Migrations 🟡

**Problem:**  
- Raw SQL migrations mixed in `init_db()`
- SQLite-specific syntax (ALTER TABLE limitations)
- No schema versioning
- Impossible to migrate to PostgreSQL later

**Proposed Solution:**
1. **Alembic** for schema migrations
2. **SQLAlchemy Core** for type-safe queries
3. **Abstract database backend** (SQLite today, PostgreSQL tomorrow)

---

### Issue #18: DEK Cache Persistence 🟡

**Problem:**  
Current DEK (Data Encryption Key) cache is in-memory Python dict:
- Lost on server restart
- Doesn't work with multiple workers (Gunicorn)
- No cross-process invalidation

**Proposed Solutions:**

**Option A:** Redis with encrypted DEK storage (recommended for production)
**Option B:** Server-side sessions in database (minimal infrastructure)
**Option C:** Encrypted session cookies (stateless)

**Decision:** Implement Option B first (extend current `sessions` table), then Option A for scale.

---

### Issue #19: Storage Abstraction 🟢

**Problem:**  
Direct filesystem operations everywhere:
```python
with open(UPLOADS_DIR / filename, "wb") as f:
    f.write(content)
```

Cannot easily switch to S3, MinIO, or network storage.

**Proposed Solution:**
```python
app/infrastructure/storage/
├── base.py              # Storage protocol
├── local_storage.py     # Filesystem implementation
├── s3_storage.py        # S3 implementation
└── encrypted_storage.py # Encryption wrapper
```

---

### Issue #20: CSRF Security Hardening 🟢

**Problem:**  
CSRF cookie uses insecure settings:
```python
response.set_cookie(
    key=CSRF_COOKIE_NAME,
    secure=False,  # Should be True in production
    httponly=False,
)
```

**Fix:** Environment-based configuration
```python
secure = os.environ.get("ENV") == "production"
response.set_cookie(..., secure=secure, httponly=True)
```

---

### Issue #21: Pydantic Request Validation 🔵

**Problem:**  
Form data not validated:
```python
@router.post("/upload")
async def upload(folder_id: str = Form(None)):  # No validation!
```

**Fix:** Pydantic models for all endpoints
```python
class UploadRequest(BaseModel):
    folder_id: UUID
    file: UploadFile
    
    @validator('file')
    def validate_size(cls, v):
        if v.size > MAX_SIZE:
            raise ValueError("File too large")
```

---

### Issue #23: Unified File Access Service 🔴 🔫

**Status:** **IN PROGRESS** - 2026-03-01

**Problem:**  
Parallel file access paths for regular files and E2E-encrypted (Safe) files cause massive code duplication:

| Aspect | Regular Files | Safe Files (E2E) | Result |
|--------|---------------|------------------|--------|
| URL Pattern | `/uploads/{id}` | Same URL, but handled differently | Confusion |
| Encryption | Server-side (DEK) | Client-side (Safe DEK) | Dual logic |
| Thumbnails | Direct `<img src>` | Blob URL after decryption | Branching |
| Backend checks | `is_encrypted` | `safe_id` checks | Duplication |

**Code Duplication Examples:**
- `app/routes/gallery/files.py`: 90+ lines checking `if photo.get("safe_id")` vs `if photo["is_encrypted"]`
- `app/static/js/navigation.js`: Two separate HTML templates for photos (lines 282-346)
- `app/static/js/gallery-lightbox.js`: Branching logic for loading (lines 723-820)
- `app/static/js/safes.js`: Separate thumbnail loading logic entirely

**Core Principles:**
1. **True E2E**: Safe files are decrypted ONLY on client - server never has Safe DEK
2. **Unified Interface**: Single way to access files regardless of encryption type
3. **Transparent Handling**: Client decides how to handle based on metadata, not URL

**Solution Architecture:**

```
┌─────────────────────────────────────────────────────────────────┐
│              UNIFIED FILE ACCESS SERVICE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│   ┌────────────────┐    ┌────────────────┐    ┌────────────────┐       │
│   │   Regular    │    │   Server-Side │    │      E2E      │       │
│   │    Files     │◄────│   Encrypted   │◄────│    (Safes)    │       │
│   │              │    │               │    │               │       │
│   └────────────────┘    └────────────────┘    └────────────────┘       │
│          │                  │                  │              │
│          └───────────────────────────────────────────────────┘              │
│                              │                                 │
│                              ▼                                 │
│   ┌────────────────────────────────────────────────────────────┐  │
│   │  Backend: /files/{id} - streams raw bytes + metadata       │  │
│   │  (server decrypts server-side, leaves E2E as-is)             │  │
│   └────────────────────────────────────────────────────────────┘  │
│                              │                                 │
│   ┌────────────────────────────────────────────────────────────┐  │
│   │  Frontend: FileAccessService.getFileUrl(photoId)            │  │
│   │  (returns URL or Blob URL based on encryption type)         │  │
│   └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation Plan:**

#### Phase 1: Backend Simplification
- [ ] Create unified `/files/{id}` endpoint (replaces `/uploads/{id}` logic)
- [ ] Create unified `/thumbnails/{id}` endpoint (replaces thumbnail logic)
- [ ] Remove server-side Safe decryption code (dead code, never executed)
- [ ] Standardize response headers:
  - `X-Encryption: none|server|e2e` - encryption type
  - `X-Safe-Id: {id}` - if E2E file
- [ ] Mark `/uploads/{id}` and legacy endpoints as deprecated

#### Phase 2: Frontend FileAccessService
- [ ] Create `app/static/js/file-access-service.js`:
  ```javascript
  const FileAccessService = {
      async getFileUrl(photoId, options = {}),     // Returns direct URL or Blob URL
      async getThumbnailUrl(photoId),              // Same for thumbnails
      revokeUrl(url),                              // Cleanup Blob URLs
      getPhotoMetadata(photoId)                    // Fetch metadata
  };
  ```
- [ ] Unified rendering: single HTML template for all photos
- [ ] Unified lightbox: single loading pipeline
- [ ] Remove `data-safe-thumbnail` attributes, use `data-photo-id` only

#### Phase 3: Cleanup
- [ ] Remove `safes.js` thumbnail loading logic (move to service)
- [ ] Remove `safe_id` checks from navigation.js, gallery-lightbox.js
- [ ] Delete dead server-side Safe decryption code
- [ ] Update tests

**Key Decisions:**
1. **No Server-Side Safe Decryption**: True E2E means server NEVER has Safe DEK
2. **Blob URLs for E2E**: Client fetches encrypted, decrypts, creates object URL
3. **Metadata-Driven**: Frontend decides handling based on `photo.safe_id`, not URL pattern
4. **Backward Compatible**: Old endpoints work until v1.0 with deprecation warnings

**Files To Modify:**
- `app/routes/gallery/files.py` - Simplify, remove Safe branching
- `app/routes/safe_files.py` - Keep for encrypted key delivery only
- `app/static/js/file-access-service.js` - NEW
- `app/static/js/navigation.js` - Use service
- `app/static/js/gallery-lightbox.js` - Use service
- `app/static/js/safes.js` - Remove thumbnail logic

**Success Metrics:**
| Metric | Before | After |
|--------|--------|-------|
| `safe_id` checks in frontend | 15+ places | 1 place (FileAccessService) |
| HTML templates for photo | 2 (regular/safe) | 1 unified |
| Server-side Safe decryption | Partial (dead code) | 0 (removed) |
| Lines in files.py | ~240 | ~120 (-50%) |
| Lines in navigation.js photo render | ~70 | ~35 (-50%) |

---

## Contributing

When working on these improvements:

1. **Create a feature branch:** `git checkout -b refactor/issue-N-short-name`
2. **Update this file:** Mark status as 🔄 In Progress
3. **Maintain backward compatibility:** Use deprecation warnings
4. **Add tests:** Every refactored module needs tests
5. **Update CHANGELOG.md:** Document breaking changes

---

### Issue #24: Polymorphic Items & Albums Architecture 🔴 🔫

**Status:** **IN PROGRESS** - 2026-03-01  
**Part of:** v1.0 Breaking Release

**Problem:**  
Current album architecture is tightly coupled to photos:
- `photos.album_id + position` fields couple photos to albums
- Album logic scattered across PhotoRepository (~20 methods)
- Cannot add new content types (notes, files) to albums later
- Photos vs Videos are implicit (media_type field), no clear separation

**Goal:**  
Create polymorphic item system where albums can contain any content type:
```
Album → [Photo, Video, Note, File, ...]  (any mix)
```

**Architecture (Strategy Pattern):**

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ITEMS (polymorphic)                              │
├─────────────────────────────────────────────────────────────────────┤
│  id, type, folder_id, safe_id, user_id, created_at, title, metadata   │
└─────────────────────────────────────────────────────────────────────┘
                              │
         ┌──────────────┬──────────────┬──────────────┐
         ▼                  ▼                  ▼
   ┌────────────┐    ┌────────────┐    ┌────────────┐
   │  item_media  │    │  item_notes  │    │  item_files  │  (extensible)
   │  (photo/video)│    │  (text)      │    │  (docs)      │
   └────────────┘    └────────────┘    └────────────┘
                              │
   ┌─────────────────────────────────────────────────────────────────────┐
   │                      ALBUMS                                       │
   │  id, name, folder_id, safe_id, cover_item_id, created_at          │
   └─────────────────────────────────────────────────────────────────────┘
                              │
   ┌─────────────────────────────────────────────────────────────────────┐
   │                    ALBUM_ITEMS (junction)                          │
   │  album_id, item_id, position, added_at                            │
   └─────────────────────────────────────────────────────────────────────┘
```

**Key Decisions:**

1. **Photo vs Video**:  
   Both are `item_media` with `media_type: 'image' | 'video'`. Same storage, same handling.
   ```sql
   item_media (
       item_id TEXT PRIMARY KEY,
       media_type TEXT,  -- 'image' or 'video'
       filename TEXT,
       width INTEGER,
       height INTEGER,
       duration INTEGER,  -- for video
       ...
   )
   ```

2. **Album Content-Agnostic**:  
   `album_items` связывает album_id → item_id (любого типа). Позиция хранится здесь, не в items.

3. **Safe Compatibility**:  
   Album и Items должны быть в одном safe_id (консистентность шифрования).

**Implementation Plan:**

#### Phase 1: Database Schema
- [ ] Create `items` table (polymorphic base)
- [ ] Create `item_media` table (photo/video specific)
- [ ] Create `album_items` junction table (replaces photos.album_id)
- [ ] Migration: photos → items + item_media
- [ ] Migration: photos.album_id → album_items
- [ ] Drop old columns after migration

#### Phase 2: Repository Layer
- [ ] Create `ItemRepository` (base CRUD for polymorphic items)
- [ ] Create `ItemMediaRepository` (photo/video specifics)
- [ ] Create `AlbumRepository` (albums + album_items operations)
- [ ] Remove album methods from `PhotoRepository`
- [ ] Update `UploadService` to use new repositories

#### Phase 3: Service Layer
- [ ] Create `ItemService` (unified item operations)
- [ ] Update `AlbumService` (content-agnostic album management)
- [ ] Strategy pattern for item rendering:
   ```python
   class ItemRenderer(ABC):
       @abstractmethod
       def render_thumbnail(self, item) -> str: ...
       @abstractmethod
       def render_lightbox(self, item) -> str: ...
   
   class MediaRenderer(ItemRenderer): ...  # photos/videos
   class NoteRenderer(ItemRenderer): ...   # future
   ```

#### Phase 4: API & Frontend ✅
- [x] Create `/api/items/*` endpoints (unified)
- [x] Create `/api/albums/*` endpoints (use item_ids)
- [x] Frontend: polymorphic item components (backward compat mode)
- [x] Update album editor to work with generic items

#### Phase 5: Complete Legacy Removal (Current Focus)
**Goal:** Remove all legacy photo/album coupling, use polymorphic items exclusively

**Step 1: Frontend Transition** ✅
- [x] Update `navigation.js` - render items with `type: 'item'` + `item_type`
- [x] Update `gallery-albums.js` - use `album.items` instead of `album.photos`
- [x] Update `gallery-lightbox.js` - work with `item_id` instead of `photo_id`
- [x] Update `gallery-selection.js` - unify selection for generic items

**Step 2: API Transition** ✅
- [x] Return polymorphic items from `/api/folders/{id}/content` with standalone_only filter
- [x] Fix upload-album endpoint to use optional album_name with default
- [x] Add `ItemService.get_items_by_folder(standalone_only=True)` to exclude album items
- [ ] Update `FolderRepository.get_standalone_photos()` → `get_standalone_items()`
- [ ] Remove dual-write (stop creating legacy Photo records on upload)

**Step 3: Repository Cleanup**
- [ ] Deprecate `PhotoRepository.get_album()` → use `AlbumRepository.get_by_id()`
- [ ] Deprecate `PhotoRepository.get_album_photos()` → use `AlbumRepository.get_items()`
- [ ] Deprecate `PhotoRepository.add_to_album()` → use `AlbumRepository.add_item()`
- [ ] Deprecate `PhotoRepository.remove_from_album()` → use `AlbumRepository.remove_item()`
- [ ] Deprecate `PhotoRepository.set_album_cover()` → use `AlbumRepository.set_cover_item()`
- [ ] Update `FolderRepository.get_photo_count()` → `get_item_count()`

**Step 4: Database Cleanup (Post-Stabilization)**
- [ ] Mark all legacy photos as `migrated_to_items`
- [ ] Remove `photos.album_id` column (after confirming no references)
- [ ] Archive or drop `photos` table (after full migration)

**Step 5: New Features Enabled**
```python
# Example: Adding Notes (no album changes needed!)
class NoteRenderer(ItemRenderer):
    def render_gallery_item(self, item):
        return {'type': 'note', 'preview': item['content'][:100]}

ItemService.RENDERERS['note'] = NoteRenderer()
# Albums already support any item type via album_items
```

**Files Affected:**
- `app/database.py` - new schema + migrations
- `app/infrastructure/repositories/item_repository.py` - NEW
- `app/infrastructure/repositories/item_media_repository.py` - NEW  
- `app/infrastructure/repositories/album_repository.py` - NEW
- `app/infrastructure/repositories/photo_repository.py` - refactor
- `app/application/services/item_service.py` - NEW
- `app/application/services/album_service.py` - refactor
- `app/routes/gallery/items.py` - NEW (replaces photos.py)
- `app/static/js/item-renderers.js` - NEW (Strategy Pattern)

**Breaking Changes:**
- API: `/api/photos/*` → `/api/items/*` (with type filter)
- Database: полная реструктуризация photos/albums
- Frontend: компоненты должны работать с generic items

**Migration Path:**
1. Автоматическая миграция при старте (init_db)
2. Backup перед миграцией
3. Rollback возможен только из backup

**Future Extensibility:**
```python
# Добавление Notes в v1.1:
# 1. Create item_notes table
# 2. Create NoteRenderer
# 3. Done - albums already support any item type
```

---

### Issue #28: Phase 5 - Complete Legacy Removal 🔴 🔫

**Status:** **IN PROGRESS** - 2026-03-01  
**Part of:** v1.0 Breaking Release  
**Depends on:** Issue #24 (Phases 1-4)

**Problem:**  
Current hybrid mode maintains both legacy and new systems:
- Upload creates both `Item` (new) and `Photo` (legacy) records
- Legacy `PhotoRepository` methods still used for albums
- Dual-write adds complexity and database bloat

**Goal:**  
Complete migration to polymorphic items, remove all legacy code.

#### Step 3: Repository Cleanup ✅
- [x] Deprecate `PhotoRepository.get_album()` → use `AlbumRepository.get_by_id()`
- [x] Deprecate `PhotoRepository.get_album_photos()` → use `AlbumRepository.get_items()`
- [x] Deprecate `PhotoRepository.add_to_album()` → use `AlbumRepository.add_item()`
- [x] Deprecate `PhotoRepository.remove_from_album()` → use `AlbumRepository.remove_item()`
- [x] Deprecate `PhotoRepository.set_album_cover()` → use `AlbumRepository.set_cover_item()`
- [x] Update `FolderRepository.get_photo_count()` → `get_item_count()`

#### Step 4: Remove Dual-Write ✅
- [x] Stop creating legacy Photo records on upload
- [x] Mark legacy photos as `migrated_to_items` in database
- [x] Ensure all queries use `items` table exclusively

#### Step 5: Database Cleanup (Post-Stabilization) ⏸️ STOPPED
**Status:** Awaiting user confirmation before proceeding

- [ ] Remove `photos.album_id` column after confirming no references
- [ ] Archive or drop `photos` table after full migration
- [ ] Update foreign key references

**Note:** Step 5 involves destructive database changes. Legacy `photos` table data should be verified backed up before proceeding.

**Acceptance Criteria:**
- [ ] No legacy photo methods used in new code
- [ ] Upload creates only Item records
- [ ] All navigation works with polymorphic items
- [ ] Tests pass without legacy fallbacks
- [ ] Database size reduced (no duplicate records)

**Files to Modify:**
- `app/infrastructure/repositories/photo_repository.py` - remove album methods
- `app/infrastructure/repositories/folder_repository.py` - use items table
- `app/routes/gallery/uploads.py` - remove dual-write
- `app/database.py` - migration for marking legacy photos

---

## Notes

- **Backward Compatibility:** All changes must maintain API compatibility until v1.0
- **Deprecation Strategy:** Old functions proxy to new with `warnings.warn()` until v1.0
- **Testing:** See `tests/` directory for integration test requirements
- **Performance:** Each change should include before/after benchmarks
