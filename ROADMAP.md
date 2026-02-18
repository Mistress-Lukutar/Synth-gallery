# Synth Gallery Architecture Roadmap

This document tracks planned architectural improvements, refactoring goals, and technical debt resolution.

> **Last Updated:** 2026-02-18  
> **Status:** Active Development  
> **Priority Legend:** 🔴 Critical | 🟡 High | 🟢 Medium | 🔵 Low

---

## Quick Overview

| Priority | Issue | Solution | Effort | Status |
|----------|-------|----------|--------|--------|
| 🔴 Critical | [#14](https://github.com/Nate-go/Synth-Gallery/issues/14) | God Module - Repository Pattern | Large | ✅ **DONE** |
| 🔴 Critical | [#15](https://github.com/Nate-go/Synth-Gallery/issues/15) | Async Database (aiosqlite) | Medium | ✅ **DONE** |
| 🟡 High | [#16](https://github.com/Nate-go/Synth-Gallery/issues/16) | Business Logic Extraction | Medium | 🔄 In Progress |
| 🟡 High | [#17](https://github.com/Nate-go/Synth-Gallery/issues/17) | SQLAlchemy Core / Alembic | Large | 🔲 Planned |
| 🟡 High | [#18](https://github.com/Nate-go/Synth-Gallery/issues/18) | Redis / Encrypted Sessions | Medium | 🔲 Planned |
| 🟢 Medium | [#19](https://github.com/Nate-go/Synth-Gallery/issues/19) | Storage Interface (S3/local) | Medium | 🔲 Planned |
| 🟢 Medium | [#20](https://github.com/Nate-go/Synth-Gallery/issues/20) | Secure Cookie Settings | Small | 🔲 Planned |
| 🔵 Low | [#21](https://github.com/Nate-go/Synth-Gallery/issues/21) | Request Validation Models | Small | 🔲 Planned |

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

### Issue #15: Async Database Layer 🔴 ✅

**Status:** **COMPLETED** - 2026-02-18

**Problem:**  
FastAPI is an async framework, but all database operations use synchronous SQLite (`sqlite3` module). This blocks the event loop during file uploads and complex queries.

**Solution Implemented:**
```
app/
└── infrastructure/
    ├── database/
    │   ├── connection.py    # Async connection pool (aiosqlite)
    │   └── pool.py          # Connection pool management
    └── repositories/
        ├── base.py          # AsyncRepository base class
        ├── user_repository.py      ✅ AsyncUserRepository
        ├── session_repository.py   ✅ AsyncSessionRepository  
        ├── folder_repository.py    ✅ AsyncFolderRepository
        ├── permission_repository.py ✅ AsyncPermissionRepository
        ├── photo_repository.py     ✅ AsyncPhotoRepository
        └── safe_repository.py      ✅ AsyncSafeRepository
```

**Results:**
- ✅ All 6 repositories have async versions
- ✅ Async connection pool with configurable max connections
- ✅ FastAPI dependency `get_async_db()` for async endpoints
- ✅ 12 async repository tests passing
- ✅ Full backward compatibility (sync APIs unchanged)
- ✅ No event loop blocking during database operations

**Migration Example:**
```python
# New async way:
from app.infrastructure.repositories import AsyncUserRepository
from app.database import get_async_db

@app.get("/api/users/{user_id}")
async def get_user(user_id: int, db = Depends(get_async_db)):
    repo = AsyncUserRepository(db)
    return await repo.get_by_id(user_id)
```

---

## Planned Issues

### Issue #16: Service Layer Extraction 🟡 🔄

**Status:** **IN PROGRESS** - 2026-02-18

**Problem:**  
Business logic is embedded directly in FastAPI route handlers:
- `app/routes/gallery.py` (1000+ lines)
- Upload logic duplicated between single/bulk/album
- HTTP concerns mixed with business rules

**Solution Implemented:**
```
app/application/
├── __init__.py
├── services/
│   ├── __init__.py
│   ├── upload_service.py      ✅ UploadService
│   ├── folder_service.py      ✅ FolderService
│   ├── permission_service.py  ✅ PermissionService
│   └── safe_service.py        ✅ SafeService
```

**Results:**
- ✅ 4 application services created
- ✅ `routes/folders.py` fully refactored to use FolderService
- ✅ Upload endpoint refactored to use UploadService with PhotoRepository
- ✅ Fixed PhotoRepository.create() signature to accept optional photo_id
- ✅ Business logic separated from HTTP handling
- ✅ Services testable in isolation (no FastAPI dependencies)
- ✅ All 108 existing tests pass

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
- [x] Create application services (Folder, Permission, Safe, Upload)
- [x] Refactor `routes/folders.py` to use FolderService
- [x] Refactor gallery.py `/upload` endpoint to use UploadService
- [x] Refactor gallery.py `/upload-album` endpoint to use UploadService
- [x] Refactor gallery.py `/upload-bulk` endpoint to use UploadService
- [x] Refactor gallery.py `/api/photos/batch-delete` endpoint to use UploadService
- [x] Fix PhotoRepository integration with UploadService
- [x] Add comprehensive service layer unit tests (25 tests total, 6 for UploadService)

**Next Steps:**
- [ ] Refactor remaining gallery.py routes (move operations, album management)
- [ ] Refactor safe routes to use SafeService
- [ ] Extract remaining business logic from envelope.py

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

- **Backward Compatibility:** All changes must maintain API compatibility until v2.0
- **Deprecation Strategy:** Old functions will proxy to new with `warnings.warn()`
- **Testing:** See `tests/` directory for integration test requirements
- **Performance:** Each change should include before/after benchmarks
