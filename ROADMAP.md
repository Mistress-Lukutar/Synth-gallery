# Synth Gallery Architecture Roadmap

This document tracks planned architectural improvements, refactoring goals, and technical debt resolution.

> **Last Updated:** 2026-02-17  
> **Status:** Active Development  
> **Priority Legend:** 🔴 Critical | 🟡 High | 🟢 Medium | 🔵 Low

---

## Quick Overview

| Priority | Issue | Solution | Effort | Status |
|----------|-------|----------|--------|--------|
| 🔴 Critical | [#1](#issue-1-god-module-refactoring) | God Module - Repository Pattern | Large | 🔲 Planned |
| 🔴 Critical | [#2](#issue-2-async-database-layer) | Async Database (aiosqlite) | Medium | 🔲 Planned |
| 🟡 High | [#3](#issue-3-service-layer) | Business Logic Extraction | Medium | 🔲 Planned |
| 🟡 High | [#4](#issue-4-database-abstraction) | SQLAlchemy Core / Alembic | Large | 🔲 Planned |
| 🟡 High | [#5](#issue-5-dek-cache-persistence) | Redis / Encrypted Sessions | Medium | 🔲 Planned |
| 🟢 Medium | [#6](#issue-6-storage-abstraction) | Storage Interface (S3/local) | Medium | 🔲 Planned |
| 🟢 Medium | [#7](#issue-7-csrf-security-hardening) | Secure Cookie Settings | Small | 🔲 Planned |
| 🔵 Low | [#8](#issue-8-pydantic-validation) | Request Validation Models | Small | 🔲 Planned |

---

## Detailed Issues

### Issue #1: God Module Refactoring 🔴

**Problem:**  
The `app/database.py` file has grown to 2100+ lines, containing schema definitions, migrations, CRUD operations for all entities, business logic, and encryption key management. This creates:
- Difficult testing (can't test user logic without importing everything)
- Merge conflicts (every feature touches the same file)
- Cognitive overhead (developers must understand entire module)
- No clear boundaries between domains

**Current State:**
```
app/database.py (2136 lines)
├── User management (100 lines)
├── Session management (80 lines)
├── Folder CRUD (300 lines)
├── Photo CRUD (400 lines)
├── Album CRUD (200 lines)
├── Permission logic (250 lines)
├── Encryption keys (200 lines)
├── WebAuthn (200 lines)
├── Safes (400 lines)
└── Schema migrations (mixed throughout)
```

**Proposed Solution:**
```
app/
├── infrastructure/
│   ├── database/
│   │   ├── connection.py      # Thread-local/async connection management
│   │   ├── migrations.py      # Schema versioning
│   │   └── transactions.py    # Transaction context managers
│   └── repositories/
│       ├── base.py            # Abstract repository interface
│       ├── user_repo.py       # UserRepository class
│       ├── folder_repo.py     # FolderRepository class
│       ├── photo_repo.py      # PhotoRepository class
│       ├── album_repo.py      # AlbumRepository class
│       └── safe_repo.py       # SafeRepository class
```

**Acceptance Criteria:**
- [ ] Each repository < 200 lines of code
- [ ] No raw SQL in route handlers
- [ ] Unit tests can mock repositories
- [ ] Database module deprecated (backward compatibility wrappers)

**Related Files:** `app/database.py`

---

### Issue #1: Async Database Layer 🔴

**Problem:**  
FastAPI is an async framework, but all database operations use synchronous SQLite (`sqlite3` module). This blocks the event loop during:
- File uploads (database writes)
- Gallery listing (complex recursive queries)
- Bulk operations

**Impact:**
```python
# Current - blocks the entire server
@app.get("/api/folders/{id}/content")
def get_folder(id: str):
    db = get_db()  # ❌ Blocks event loop
    photos = db.execute("SELECT * FROM photos...")  # ❌ I/O wait blocks all requests
```

**Proposed Solution:**
Migrate to `aiosqlite` or `databases` library with SQLAlchemy Core.

```python
# Target - non-blocking
@app.get("/api/folders/{id}/content")
async def get_folder(id: str):
    async with get_db() as db:
        photos = await db.fetch_all("SELECT * FROM photos...")  # ✅ Yields control
```

**Acceptance Criteria:**
- [ ] All route handlers use `async def`
- [ ] Database connections don't block event loop
- [ ] Connection pooling implemented
- [ ] 50+ concurrent upload test passes

**Migration Strategy:**
1. Phase 1: Wrap sync calls in `run_in_threadpool` (quick fix)
2. Phase 2: Introduce `aiosqlite` for new endpoints
3. Phase 3: Migrate existing endpoints incrementally

---

### Issue #2: Service Layer Extraction 🟡

**Problem:**  
Business logic is embedded directly in FastAPI route handlers:
- `app/routes/gallery.py` (1000+ lines)
- `app/routes/folders.py` (mixed concerns)
- Upload logic duplicated between single/bulk/album uploads

**Example of Current Issue:**
```python
# routes/gallery.py
@router.post("/upload")
async def upload_photo(...):
    # Validation logic here
    # Encryption logic here  
    # Thumbnail generation here
    # Database insert here
    # File system write here
    # Error handling scattered
```

**Proposed Solution:**
Introduce Service Layer (Application Services pattern):

```python
app/application/
├── services/
│   ├── upload_service.py      # FileUploadService
│   ├── folder_service.py      # FolderManagementService
│   ├── permission_service.py  # AccessControlService
│   └── encryption_service.py  # EncryptionOrchestrator
└── dto/
    ├── upload_request.py
    └── upload_result.py
```

**Benefits:**
- Routes become thin (only HTTP concern)
- Business logic testable without HTTP client
- Can reuse services in CLI scripts (`manage_users.py`)
- Clear transaction boundaries

---

### Issue #4: Database Abstraction & Migrations 🟡

**Problem:**  
- Raw SQL migrations mixed in `init_db()` function
- SQLite-specific syntax (`ALTER TABLE` limitations)
- No schema versioning
- Impossible to migrate to PostgreSQL later

**Current Migration Hell:**
```python
# database.py - mixed schema and migrations
def init_db():
    db.execute("CREATE TABLE IF NOT EXISTS...")  # Schema
    # Migration 1: add column
    if "column" not in columns:
        db.execute("ALTER TABLE...")
    # Migration 2: recreate table
    if wrong_type:
        db.execute("CREATE TABLE new...")  # Complex data migration
```

**Proposed Solution:**
1. **Alembic** for schema migrations
2. **SQLAlchemy Core** for type-safe queries
3. **Abstract database backend** (SQLite today, PostgreSQL tomorrow)

```python
# migrations/versions/001_initial.py (Alembic)
# migrations/versions/002_add_encryption.py

# repositories use SQLAlchemy Core
from sqlalchemy import select
stmt = select(photos_table).where(photos_table.c.folder_id == folder_id)
```

**Acceptance Criteria:**
- [ ] Alembic configured and working
- [ ] No schema changes in application code
- [ ] `docker-compose` includes migration step
- [ ] Rollback capability for failed migrations

---

### Issue #5: DEK Cache Persistence 🟡

**Problem:**  
Current DEK (Data Encryption Key) cache is in-memory Python dict:
- Lost on server restart → users must re-enter passwords
- Doesn't work with multiple workers (Gunicorn)
- No invalidation mechanism when password changes
- TTL not enforced across processes

```python
# Current (app/services/encryption.py)
dek_cache = DEKCache()  # Simple dict
```

**Proposed Solutions:**

**Option A: Redis (Recommended for production)**
- Encrypted DEK stored in Redis with TTL
- Shared across all workers
- Survives server restart

**Option B: Encrypted Session Cookies**
- DEK encrypted with session key, stored client-side
- Server stateless
- Limited by cookie size (4KB)

**Option C: Server-side Sessions in DB**
- `sessions` table extended with `encrypted_dek` field
- Similar to current `safe_sessions` implementation

**Decision:** Implement Option C first (minimal infrastructure), then Option A for scale.

---

### Issue #6: Storage Abstraction 🟢

**Problem:**  
Direct filesystem operations everywhere:
```python
with open(UPLOADS_DIR / filename, "wb") as f:
    f.write(content)
```

Cannot easily switch to:
- S3 / MinIO
- NFS / Network storage
- Encrypted volumes

**Proposed Solution:**
```python
app/infrastructure/storage/
├── base.py              # AbstractStorage interface
├── local_storage.py     # Filesystem implementation
├── s3_storage.py        # S3 implementation
└── encrypted_storage.py # Wrapper for encryption
```

**Interface:**
```python
class Storage(Protocol):
    async def write(self, path: str, data: bytes) -> None: ...
    async def read(self, path: str) -> bytes: ...
    async def delete(self, path: str) -> None: ...
    async def exists(self, path: str) -> bool: ...
```

**Benefits:**
- Test with in-memory storage (fast tests)
- Easy cloud migration
- Mock for unit tests

---

### Issue #7: CSRF Security Hardening 🟢

**Problem:**  
CSRF cookie is not secure:
```python
response.set_cookie(
    key=CSRF_COOKIE_NAME,
    value=token,
    httponly=False,
    samesite="lax",
    secure=False,  # ❌ Should be True in production
)
```

**Fix:** Environment-based configuration
```python
secure = os.environ.get("ENV") == "production"
response.set_cookie(..., secure=secure, httponly=True)
```

---

### Issue #8: Pydantic Request Validation 🔵

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

## Implementation Order

### Phase 1: Foundation (Weeks 1-2)
1. **Issue #7** (CSRF) - Security fix, 1 day
2. **Issue #8** (Validation) - Prevents bad data, 2 days
3. **Tests** - Integration test suite (see `tests/` directory)

### Phase 2: Architecture (Weeks 3-6)
4. **Issue #1** (God Module) - Break down `database.py`, 2 weeks
5. **Issue #3** (Service Layer) - Extract business logic, 1 week

### Phase 3: Infrastructure (Weeks 7-10)
6. **Issue #4** (Migrations) - Alembic setup, 1 week
7. **Issue #2** (Async DB) - Non-blocking I/O, 2 weeks
8. **Issue #5** (DEK Cache) - Production-ready sessions, 1 week

### Phase 4: Scale (Weeks 11-12)
9. **Issue #6** (Storage) - Cloud-ready storage, 1 week

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
