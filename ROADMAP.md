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
| 🟡 High     | [#22](https://github.com/Nate-go/Synth-Gallery/issues/22) | Album Entity Refactoring        | Medium | 🔲 Planned     |
| 🟡 High     | [#17](https://github.com/Nate-go/Synth-Gallery/issues/17) | SQLAlchemy Core / Alembic       | Large  | 🔲 Planned     |
| 🟡 High     | [#18](https://github.com/Nate-go/Synth-Gallery/issues/18) | Redis / Encrypted Sessions      | Medium | 🔲 Planned     |
| 🟢 Medium   | [#19](https://github.com/Nate-go/Synth-Gallery/issues/19) | Storage Interface (S3/local)    | Medium | 🔲 Planned     |
| 🟢 Medium   | [#20](https://github.com/Nate-go/Synth-Gallery/issues/20) | Secure Cookie Settings          | Small  | 🔲 Planned     |
| 🔵 Low      | [#21](https://github.com/Nate-go/Synth-Gallery/issues/21) | Request Validation Models       | Small  | 🔲 Planned     |

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

## Contributing

When working on these improvements:

1. **Create a feature branch:** `git checkout -b refactor/issue-N-short-name`
2. **Update this file:** Mark status as 🔄 In Progress
3. **Maintain backward compatibility:** Use deprecation warnings
4. **Add tests:** Every refactored module needs tests
5. **Update CHANGELOG.md:** Document breaking changes

---

## Notes

- **Backward Compatibility:** All changes must maintain API compatibility until v1.0
- **Deprecation Strategy:** Old functions proxy to new with `warnings.warn()` until v1.0
- **Testing:** See `tests/` directory for integration test requirements
- **Performance:** Each change should include before/after benchmarks
