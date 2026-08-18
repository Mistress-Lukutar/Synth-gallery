# Synth Gallery - Agent Documentation

> **For AI Coding Agents**: This document provides essential context for working with the Synth Gallery codebase. Read this before making any changes.

## Project Overview

Synth Gallery is a **personal media vault** with end-to-end encryption, hardware key authentication, and multi-user support. It allows users to securely store, organize, and share photos and videos.

### Key Features
- **Universal Server-side Encryption**: All media files are encrypted with AES-256-GCM using the owner's DEK on upload
- **Hardware Key Login**: WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Folder Hierarchy**: Nested folders with sharing support (Viewer/Editor permissions)
- **Albums**: Group related media with drag-and-drop reordering
- **Tags & Search**: Categorize and find content quickly
- **Backup & Recovery**: Full backups with integrity verification and recovery keys

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Web Framework | FastAPI 0.129.0 |
| Server | Uvicorn 0.41.0 |
| Database | SQLite (sqlite3 module) |
| Templates | Jinja2 3.1.6 |
| Styling | Vanilla CSS |
| Frontend JS | Vanilla JavaScript (modular) |
| Image Processing | Pillow 12.1.1 |
| Video Processing | ffmpeg / ffprobe (external binary; gyan.dev full build) |
| Encryption | cryptography (AES-256-GCM, chunked streaming envelope) |
| Password Hashing | bcrypt via passlib |
| Hardware Keys | webauthn >=2.0.0 (FIDO2) |

## Project Structure

```
Synth-Gallery/
├── app/                          # Main application
│   ├── main.py                   # FastAPI entry point, lifespan management
│   ├── config.py                 # Configuration constants, environment variables
│   ├── database.py               # DB connections, schema init, password hashing
│   ├── middleware.py             # AuthMiddleware, CSRFMiddleware
│   ├── dependencies.py           # FastAPI dependencies (get_current_user, etc.)
│   ├── tags.py                   # Tag dictionary for photo suggestions
│   ├── application/              # Application services (business logic)
│   │   └── services/
│   │       ├── auth_service.py       # Authentication, sessions, DEK management
│   │       ├── folder_service.py     # Folder CRUD, tree operations
│   │       ├── permission_service.py # Access control logic
│   │       ├── item_service.py       # Item (photo/video) operations
│   │       ├── item_types.py         # Typed item-type registry (single source of truth)
│   │       ├── item_renderers.py     # Strategy renderers per item type
│   │       ├── album_service.py      # Album CRUD and operations
│   │       └── user_settings_service.py # User preferences
│   ├── infrastructure/           # Infrastructure layer
│   │   ├── repositories/         # Repository pattern (DB operations)
│   │   │   ├── base.py               # Repository base class
│   │   │   ├── user_repository.py
│   │   │   ├── session_repository.py
│   │   │   ├── folder_repository.py
│   │   │   ├── permission_repository.py
│   │   │   ├── item_repository.py    # Polymorphic items (photos, videos, etc.)
│   │   │   ├── item_media_repository.py  # Media-specific data
│   │   │   ├── album_repository.py
│   │   │   └── webauthn_repository.py
│   │   ├── services/             # Infrastructure services
│   │   │   ├── encryption.py         # Chunked AES-256-GCM streaming envelope, DEK cache
│   │   │   ├── backup.py             # Backup/restore service + scheduler
│   │   │   ├── ffmpeg.py             # ffmpeg/ffprobe wrappers (probe + thumbnail)
│   │   │   ├── media.py              # Image processing (Pillow only)
│   │   │   ├── image_conversion.py   # Download-time image conversion (JXL/JPEG/PNG/WebP)
│   │   │   ├── metadata.py           # EXIF/metadata extraction
│   │   │   ├── thumbnail.py          # Thumbnail generation/regeneration
│   │   │   └── webauthn.py           # Hardware key support
│   │   └── storage/              # Storage abstraction layer
│   │       ├── base.py               # StorageInterface
│   │       ├── local_storage.py      # Filesystem backend
│   │       ├── s3_storage.py         # S3/MinIO backend
│   │       └── factory.py            # get_storage() factory
│   ├── routes/                   # API routes
│   │   ├── auth.py                   # Login/logout
│   │   ├── admin.py                  # Admin panel (users, backups)
│   │   ├── api.py                    # AI service endpoints
│   │   ├── folders.py                # Folder management
│   │   ├── tags.py                   # Tag management
│   │   ├── webauthn.py               # Hardware key registration/auth
│   │   ├── envelope.py               # Envelope encryption
│   │   ├── user_settings.py          # User profile settings
│   │   └── gallery/                  # Gallery routes
│   │       ├── main.py               # Main gallery view
│   │       ├── items.py              # Item (photo/video) operations
│   │       ├── files.py              # File serving
│   │       ├── uploads.py            # Upload handling
│   │       └── deps.py               # Gallery dependencies
│   ├── static/                   # Static assets
│   │   ├── style.css
│   │   └── js/
│   │       ├── core.js               # Core utilities
│   │       ├── init.js               # Initialization
│   │       ├── navigation.js         # Navigation
│   │       ├── upload.js             # Upload handling
│   │       └── gallery-*.js          # Gallery features
│   └── templates/                # Jinja2 templates
│       ├── base.html
│       ├── login.html
│       ├── gallery.html
│       ├── settings.html
│       ├── encryption_settings.html
│       ├── admin_backups.html
│       └── admin_maintenance.html
├── tests/                        # Test suite
│   ├── conftest.py               # pytest fixtures
│   ├── integration/              # Integration tests
│   └── unit/                     # Unit tests
├── uploads/                      # Uploaded files (server-side encrypted by default)
├── thumbnails/                   # Generated thumbnails (server-side encrypted by default)
├── .agents/                      # Maintenance scripts and plans
│   ├── encrypt_existing_uploads.py   # Migration: encrypt legacy plaintext files
│   └── reencrypt_to_chunked.py       # Migration: legacy whole-file GCM → SGE1 chunked envelope
├── backups/                      # Backup storage
├── gallery.db                    # SQLite database
├── pyproject.toml                # Project configuration and dependencies (PEP 621)
├── Start.bat                     # Windows startup script
└── AGENTS.md                     # This file
```

## Architecture Patterns

### 1. Repository Pattern
All database operations go through repository classes:

```python
# CORRECT - Use repositories
from app.infrastructure.repositories import UserRepository
from app.database import create_connection

db = create_connection()
try:
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
finally:
    db.close()

# INCORRECT - Don't use raw SQL in routes
# db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### 2. Service Layer
Business logic lives in application services:

```python
# Services orchestrate repositories and implement business rules
from app.application.services import FolderService
from app.infrastructure.repositories import FolderRepository, PermissionRepository

db = create_connection()
try:
    service = FolderService(
        folder_repo=FolderRepository(db),
        permission_repo=PermissionRepository(db)
    )
    folder = service.create_folder(name, user_id, parent_id)
finally:
    db.close()
```

### 3. Database Connection Management
**CRITICAL**: Always use `create_connection()` for new connections and close them:

```python
# CORRECT - Explicit connection with cleanup
db = create_connection()
try:
    # Use db...
    repo = UserRepository(db)
    user = repo.get_by_id(1)
finally:
    db.close()

# For middleware/lifespan where request context matters:
from app.database import get_db
# get_db() returns thread-local connection - DO NOT CLOSE
```

### 4. Encryption Architecture

```
User Password / Hardware Key
    │
    ▼
PBKDF2-SHA256 (600k iterations)
    │
    ▼
KEK (Key Encryption Key) ───┐
    │                        │
    ▼                        │
DEK (Data Encryption Key) ◄──┘
    │
    ├──► File 1: chunked AEAD envelope encrypted with DEK
    └──► File 2: chunked AEAD envelope encrypted with DEK
```

- **DEK (Data Encryption Key)**: Per-user, 256-bit random, cached in memory during session
- **KEK (Key Encryption Key)**: Derived from password via PBKDF2; wraps the DEK via a single whole-file AES-256-GCM envelope (small payload, no streaming needed)
- **Files**: Encrypted with the **chunked streaming AEAD envelope** described below; supports arbitrary file sizes and HTTP Range serving
- **Legacy format**: The old whole-file `[12B nonce][ciphertext+tag]` envelope was removed. Run `.agents/reencrypt_to_chunked.py` once before starting the new service to migrate existing files

#### Chunked AEAD Envelope (SGE1)

Every media file (uploads, thumbnails, JXL fallbacks) is stored on disk in the
same chunked format so a single code path handles small thumbnails and
multi-GiB videos:

```
[MAGIC "SGE1" 4B][VERSION 1B][RESERVED 1B][CHUNK_SIZE 4B BE]
for each plaintext chunk (CHUNK_SIZE bytes, last may be shorter):
    [NONCE 12B][CIPHERTEXT + 16B GCM TAG]
```

- Each chunk is independently decryptable (own nonce + tag), so HTTP Range
  requests decrypt only the chunks overlapping `[start, end]`
- Memory usage is O(CHUNK_SIZE) regardless of file size — full plaintext is
  never held in memory
- Default `CHUNK_SIZE` is 1 MiB (configurable via `SYNTH_ENCRYPTION_CHUNK_SIZE`)
- Envelope helpers live in `app/infrastructure/services/encryption.py`:
  - `encrypt_to_stream(reader, writer, dek)` / `decrypt_to_stream(...)`
  - `iter_decrypt(reader, dek)` — lazy plaintext iterator (for `StreamingResponse`)
  - `decrypt_range(reader, dek, start, end)` — for HTTP Range serving
  - `get_plaintext_size(encrypted_size)` — for `Content-Length` / `Content-Range`
  - `encrypt_bytes` / `decrypt_bytes` — convenience for small objects (thumbnails, JXL fallbacks)

### 5. Encryption Behavior

**Uploads:**
- `process_media_upload()` streams the incoming upload to a plaintext temp file, probes metadata, generates a thumbnail, and stream-encrypts directly into storage. Memory usage is bounded by the chunk size regardless of file size
- Thumbnails are also encrypted with the same DEK via `encrypt_bytes`
- Upload fails with 403 if the user's DEK is not available

**File Serving:**
- `GET /files/{item_id}` streams the plaintext on the fly via `iter_decrypt` (full file) or `decrypt_range` (HTTP Range, returns 206 Partial Content with `Content-Range` / `Accept-Ranges`)
- `HEAD /files/{item_id}` returns `Content-Length` and `Accept-Ranges` so browsers can probe before requesting byte ranges
- The old plaintext-fallback branch was removed; all files are expected to be in SGE1 format after migration

### 5b. MKV / Large File Support

- **Allowed MIME types**: `video/x-matroska`, `video/x-mkv`, plus the existing `video/mp4`, `video/webm`
- **No upload size cap**: the streaming pipeline handles multi-GiB files. Any hard limit is the responsibility of a fronting reverse proxy (e.g. nginx `client_max_body_size`)
- **ffmpeg / ffprobe** are required for video probing and thumbnail generation. They must be on `PATH` (or pointed at via `FFMPEG_TOOL_DIR`). The gyan.dev full build (libx264/libvpx/libaom) supports MKV natively
- OpenCV (`opencv-python-headless`) was removed; all video work goes through ffmpeg
- **MKV playback in browsers**: most browsers cannot decode MKV in `<video>`. The lightbox renders a `<video>` element regardless; if it fires an `error` event (typically for MKV), an overlay is shown with a download link. Playback-capable formats (MP4 H.264/AAC, WebM) continue to play inline

**JPEG XL Experimental Storage:**
- Set `USE_JXL=true` to transcode new image uploads to lossless JPEG XL
- Encoding and decoding require the official `cjxl` / `djxl` binaries from libjxl; `Start.bat` adds them to PATH automatically
- If `cjxl` is not found, `Start.bat` downloads the latest Windows static build to `.venv\jxl-tools`
- JPEG sources are transcoded losslessly when possible; other raster formats are re-encoded losslessly
- EXIF and embedded metadata are preserved inside the JXL container
- Thumbnails continue to be generated as JPEG
- Files with `content_type == image/jxl` are served as JXL when the client sends `Accept: image/jxl`
- Browsers without JXL support receive an on-demand JPEG fallback, cached under `fallbacks/`
- The frontend uses `<picture>` with `<source type="image/jxl">` and a JPEG fallback `img`
- **ffmpeg is intentionally NOT used for JXL**: its libjxl wrapper cannot do `--lossless_jpeg` reversible transcodes, EXIF containers or progressive flags (verified against gyan.dev full builds with `--enable-libjxl`); ffmpeg stays video-only

**Batch Download & Download Conversion:**
- `POST /api/items/batch-download` takes `{item_ids, album_ids, options}`; `options.format` selects `jxl` (default = as stored), `jpeg`, `png` or `webp` plus per-format settings (quality sliders, WebP lossless, PNG optimize, JXL effort override via `encode_to_lossless_jxl(effort=...)`)
- A selection resolving to a single file is served directly with `Content-Disposition: attachment` (no ZIP); two or more files produce a spooled streaming ZIP
- Album items land in an `{AlbumName}/` subfolder (single-album downloads name the ZIP after the album); entry names are sanitized and deduplicated (`name (2).ext`)
- Conversion lives in `app/infrastructure/services/image_conversion.py` (JXL sources decoded via djxl, targets encoded via Pillow/cjxl); **videos always pass through unchanged**; failed conversions fall back to the original bytes with a warning
- The gallery frontend opens `#download-modal` (`gallery-download.js`) for format/quality selection; `gallery-selection.js` only triggers the modal
- Existing files and videos are not affected

### 6. Storage Abstraction Layer

All file operations go through the storage abstraction layer (`app/infrastructure/storage/`):

```python
# CORRECT - Use storage abstraction
from app.infrastructure.storage import get_storage

storage = get_storage()
await storage.upload(file_id, content, folder="uploads")
content = await storage.download(file_id, folder="uploads")
```

**Storage Backends:**
- **LocalStorage**: Filesystem storage (default)
- **S3Storage**: AWS S3 / MinIO / DigitalOcean Spaces

**Random access (HTTP Range / chunked-envelope seek):**
- `StorageInterface.get_random_access_reader(file_id, folder)` returns a seekable `RandomAccessReader` (read / seek / tell / close / `size`) for backends that support random access
- **LocalStorage** provides full random access (backed by an open file handle), so HTTP Range serving for videos works
- **S3Storage** currently raises `NotImplementedError` for random access — byte-range GET support is a v2.0 follow-up. Callers must fall back to whole-file streaming (`get_stream`) for S3
- Never branch on `isinstance(storage, LocalStorage)`; always go through the interface

**Configuration (Environment Variables):**
| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `S3_BUCKET` | S3 bucket name | - |
| `S3_REGION` | AWS region | `us-east-1` |
| `S3_ENDPOINT` | Custom endpoint (for MinIO) | - |
| `S3_ACCESS_KEY` | Access key | - |
| `S3_SECRET_KEY` | Secret key | - |

**Migration Notes:**
- Existing files remain in `uploads/`/`thumbnails/` when switching backends
- New files go to the configured backend
- Backups work with any backend (downloads from S3 if needed)

### 6b. Polymorphic Items (v2.0)

Items use a **polymorphic base table** with **per-type detail tables**, driven by a **typed item-type registry** and **Strategy renderers**:

```
items (base)                          item_media (detail: type='media')
├── id (PK, TEXT UUID)                ├── item_id (PK/FK → items.id)
├── type (TEXT) ◄── 'media'           ├── media_type ('image'|'video')
├── folder_id (FK)                    ├── content_type, original_name
├── user_id (FK)                      ├── width, height, duration
├── uploaded_at                       ├── thumb_width, thumb_height
├── title, description                └── taken_at, file_size, png_text_chunks
└── updated_at
```

- **`items.type`** → coarse polymorphic kind (`media`; future `note`/`audio`/`model`)
- **`item_media.media_type`** → sub-kind within media (`image` | `video`). A 3D model is not "a kind of photo", but a video *is* "a kind of media"
- **Typed registry** (`app/application/services/item_types.py`): `ITEM_TYPE_REGISTRY` maps each `items.type` to its detail table, renderer factory and allowed MIME set. Dispatch sites (file serving, upload validation, `ItemService` hydration) consult `get_item_type_spec()` / `is_known_item_type()` instead of hard-coding `'media'`. Adding a new type = a new detail table + a new renderer + one registry entry
- **Renderers** (`app/application/services/item_renderers.py`): `ItemRenderer` ABC + `MediaRenderer`. `render_gallery_item()` publishes `type`, `media_type`, `width`, `height`, `has_thumbnail`, `thumbnail_url`. The gallery frontend derives thumbnail URLs from the item id itself, so the URL fields are informational
- **Consolidated read model** (`ItemRepository.get_media_with_details`): the single `items JOIN item_media` query lives on the base repository; `ItemMediaRepository` no longer owns the JOIN

**v2.0 schema migration** (idempotent, runs automatically in `init_db()` with a pre-migration backup at `gallery.db.v2migration-bak`):
- Dropped the dead `item_media.storage_mode` column (never read)
- Dropped the redundant `item_media.filename` column (always == `item_id`; the storage key is derived from `item_id`)
- Added `CHECK (type IN ('media'))` to `items`
- Added `CHECK (media_type IN ('image', 'video'))` to `item_media`
- Legacy `media_type='3d'` rows are normalised to `'image'` (a 3D model belongs to a future `items.type`, not a media sub-kind)

**Legacy `photo_*` purge (v2.0):**
- Removed `can_access_photo`, `can_delete_photo`, `get_photo_count`, `get_standalone_photos` aliases
- API response keys renamed: `photo_count` → `item_count`, `cover_photo_id` → `cover_item_id`, request bodies `photo_ids` → `item_ids`
- File-serving path param renamed `photo_id` → `item_id` (URL is `/files/{item_id}`)

## Build and Run Commands

### Local Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run development server
uvicorn app.main:app --reload --port 8000

# With custom base URL (subpath)
set SYNTH_BASE_URL=synth  # Windows
export SYNTH_BASE_URL=synth  # Linux/macOS
uvicorn app.main:app --reload --port 8000
```

### Windows (Start.bat)
```bash
# Windows startup script with configuration
Start.bat
```

The app runs on port 8008 by default.

## Testing

### Run Tests

```bash
# Run all tests (use the project venv: .venv/Scripts/python.exe on Windows)
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/integration/test_auth.py

# Run with coverage
pytest --cov=app --cov-report=html
```

### Test Isolation

`tests/conftest.py` sets `SYNTH_DB_PATH`, `SYNTH_UPLOADS_DIR`,
`SYNTH_THUMBNAILS_DIR`, `SYNTH_FALLBACKS_DIR`, `BACKUP_PATH` and disables
the backup/tag-stats schedulers via environment variables **before** any
`app.*` module is imported. All persistent state is redirected to a
throwaway session temp directory, so tests never touch the real
`gallery.db`, `uploads/`, `thumbnails/`, `fallbacks/` or `backups/`. Each
test still gets a fresh per-test database file (`patched_config` /
`fresh_database`).

The storage factory resolves its base path from `config.UPLOADS_DIR` at
call time (`app/infrastructure/storage/factory.py`) — do not reintroduce
by-value path imports there.

E2E tests (`tests/e2e/`) drive a live server via Playwright and are
skipped automatically when `pytest-playwright` is not installed.

If old (pre-isolation) test runs left junk in production data, clean it
with `python .agents/cleanup_test_leftovers.py` (dry-run by default,
`--apply` to delete; backs up the DB to `gallery.db.cleanup-bak` first).

### Test Structure

- `conftest.py`: Contains fixtures for isolated test environments
- `integration/`: Integration tests for routes and workflows
- `unit/`: Unit tests for individual components

### Key Fixtures

- `client`: FastAPI TestClient with fresh database
- `authenticated_client`: Pre-authenticated client
- `test_user`: Created user with credentials
- `test_folder`: Created folder for testing
- `uploaded_photo`: Pre-uploaded test photo

## User Management

User management is available through the web UI at `/admin/users` (admin only):
- Create, edit, delete users
- Grant/revoke admin rights
- View user list

Profile settings (change password, recovery key, display name) are available at `/settings`.

### First Run

On first startup, if no users exist, a temporary admin account is created automatically:
- **Username:** admin
- **Password:** admin

**Important:** Log in with these credentials, then immediately create a new admin user and delete the temporary account for security.

## Configuration (Environment Variables)

| Variable | Description | Default |
|----------|-------------|---------|
| `SYNTH_DB_PATH` | SQLite database file location | `./gallery.db` |
| `SYNTH_UPLOADS_DIR` | Uploads directory location | `./uploads` |
| `SYNTH_THUMBNAILS_DIR` | Thumbnails directory location | `./thumbnails` |
| `SYNTH_FALLBACKS_DIR` | JXL JPEG-fallback cache location | `./fallbacks` |
| `SYNTH_BASE_URL` | Base URL subpath (e.g., "synth") | "" |
| `SYNTH_AI_API_KEY` | API key for AI service | None |
| `WEBAUTHN_RP_NAME` | WebAuthn display name | "Synth Gallery" |
| `BACKUP_PATH` | Backup directory path | `./backups` |
| `BACKUP_SCHEDULE` | `daily`, `weekly`, or `disabled` | `daily` |
| `BACKUP_ROTATION_COUNT` | Number of backups to keep | 5 |
| `USE_JXL` | Transcode new image uploads to lossless JPEG XL | `false` |
| `JXL_FALLBACK_QUALITY` | JPEG quality for generated fallbacks | `85` |
| `JXL_LOSSLESS_TRANSCODE_JPEG` | Use lossless JPEG transcode for JPEG sources | `true` |
| `JXL_EFFORT` | Encoder effort: 1 (fast/large) to 9 (slow/small) | `7` |
| `JXL_THREADS` | Encoder threads: -1 auto, 0 single-threaded | `-1` |
| `JXL_PROGRESSIVE_AC` | Enable `--progressive_ac` for perceived loading speed | `true` |
| `JXL_QPROGRESSIVE_AC` | Enable `--qprogressive_ac` for perceived loading speed | `true` |
| `JXL_PROGRESSIVE_DC` | Extra low-resolution pass (`--progressive_dc`), `-1` disables | `1` |
| `SYNTH_ENCRYPTION_CHUNK_SIZE` | Plaintext chunk size for the chunked AEAD envelope (bytes) | `1048576` (1 MiB) |
| `FFMPEG_TOOL_DIR` | Directory containing `ffmpeg`/`ffprobe` binaries; overrides PATH lookup | - |

## Git Commits

### Permission Required

**NEVER** run `git commit`, `git push`, `git reset`, `git rebase` or any other git mutation commands without **explicit user confirmation**.

**Typical workflow:**
1. Prepare the changes
2. Show the proposed commit message to the user
3. Wait for user confirmation (e.g., "да", "ок", "давай", "подтверждаю")
4. Only then execute the commit

### Commit Message Style

This project uses **Conventional Commits** format:

```
<type>: <description>
```

**Types:**
- `feat:` - New feature or functionality
- `fix:` - Bug fix
- `test:` - Adding or fixing tests
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting, no logic change)
- `refactor:` - Code refactoring without changing behavior

**Guidelines:**
- Use lowercase for description
- Use present tense / imperative mood ("add" not "added", "fix" not "fixed")
- Be descriptive but concise
- Reference issue numbers when applicable

**Examples from project history:**
```
feat: refactor album editor UI - unified photo grid with cover selection and drag-drop reordering
test: fix album tests to match new API response format
fix: use correct API endpoint for loading album photos
docs: add test coverage report and update documentation
style: update lightbox styling - transparent nav buttons
```

## Code Style Guidelines

### Python

1. **Type Hints**: Use type hints for function signatures
   ```python
   def get_user(self, user_id: int) -> dict | None:
       ...
   ```

2. **Docstrings**: Use triple-quoted docstrings for modules and public functions
   ```python
   """Brief description.
   
   Longer description if needed.
   
   Args:
       param: Description
       
   Returns:
       Description of return value
   """
   ```

3. **Imports**: Group imports (stdlib, third-party, local)
   ```python
   # Standard library
   import sqlite3
   from datetime import datetime
   
   # Third-party
   from fastapi import FastAPI
   
   # Local
   from ..config import BASE_DIR
   ```

4. **Database Connections**: Always use explicit connection management
   ```python
   db = create_connection()
   try:
       # operations
   finally:
       db.close()
   ```

5. **Error Handling**: Be specific with exceptions
   ```python
   try:
       result = operation()
   except sqlite3.IntegrityError:
       # Handle specific case
   except Exception as e:
       # Log unexpected errors
       raise
   ```

### JavaScript

1. **Modules**: Use ES6 modules with explicit exports
   ```javascript
   // core.js
   export function utility() { ... }
   
   // consumer.js
   import { utility } from './core.js';
   ```

2. **Event Listeners**: Use delegated events where appropriate
3. **Crypto Operations**: Use Web Crypto API for client-side encryption

## Security Considerations

### Encryption
- All files are encrypted with AES-256-GCM
- DEKs are never stored plaintext (encrypted with KEK)
- PBKDF2 uses 600,000 iterations (OWASP recommendation)

### Session Management
- HTTP-only cookies for session tokens
- SameSite=Lax CSRF protection
- 7-day session expiry
- DEK cache matches session TTL

### CSRF Protection
- Double-submit cookie pattern
- Tokens required for POST/PUT/DELETE/PATCH
- Exemptions: login page, API with separate auth

### WebAuthn/Hardware Keys
- Keys are bound to origin (domain)
- Supports multiple keys per user
- ECDSA and RSA signature algorithms

### Important Security Notes

1. **HTTPS Required**: Web Crypto API requires secure context (HTTPS or localhost)
2. **Recovery Keys**: Generate and store offline - lost key = lost data
3. **Backup Security**: Backups contain encrypted content but plaintext metadata

## Common Tasks

### Adding a New Route

1. Create route file in `app/routes/`
2. Use repository pattern for DB access
3. Add CSRF token to forms via `get_csrf_token(request)`
4. Include router in `app/main.py`

### Adding a Repository

1. Inherit from `Repository` base class
2. Implement CRUD operations
3. Add to `app/infrastructure/repositories/__init__.py`

### Adding a Service

1. Create in `app/application/services/`
2. Accept repositories in `__init__`
3. Add to `app/application/services/__init__.py`

### Database Schema Changes

1. Modify `init_db()` in `app/database.py`
2. Add migration logic if needed (pre-migration backup is automatic)
3. Update relevant repository methods

## Troubleshooting

### "No module named 'app'"
Run from project root, not from app directory.

### Database locked errors
Ensure you're using `create_connection()` and closing properly.

### Session/DEK not persisting
Check cookie settings and DEK cache TTL.

### WebAuthn not working
Must use HTTPS or localhost (Web Crypto API requirement).

## Base URL / Subpath Configuration

The application supports running under a subpath (e.g., `localhost/synth/`):

```bash
# Windows PowerShell
$env:SYNTH_BASE_URL = "synth"

# Windows CMD
set SYNTH_BASE_URL=synth

# Linux/macOS
export SYNTH_BASE_URL=synth
```

| SYNTH_BASE_URL | Resulting URL |
|----------------|---------------|
| (empty) | `http://localhost:8000/login` |
| `synth` | `http://localhost:8000/synth/login` |
| `gallery/v2` | `http://localhost:8000/gallery/v2/login` |

**Note**: No leading or trailing slashes.

---

*This document should be updated when architectural changes are made.*
