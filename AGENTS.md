# Synth Gallery - Agent Documentation

> **For AI Coding Agents**: This document provides essential context for working with the Synth Gallery codebase. Read this before making any changes.

## Project Overview

Synth Gallery is a **personal media vault** with server-side encryption, hardware key authentication, and multi-user support. It allows users to securely store, organize, and share photos, videos and text notes.

### Key Features
- **Universal Server-side Encryption**: All media files are encrypted with AES-256-GCM using the owner's DEK on upload
- **Hardware Key Login**: WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Folder Hierarchy**: Nested folders with sharing support (Viewer/Editor permissions)
- **Albums**: Group related media with drag-and-drop reordering
- **Tags & Search**: Categorize and find content quickly; tags carry Markdown descriptions (AI-editable via `set_tag_descriptions`, `[[tag]]` wiki-links); PMI-based tag suggestions learn from accept/reject feedback — a rejected tag is hidden from that item's suggestions for good
- **AI Chat**: In-site assistant sidebar with server-side tool-call agent loop; per-user LLM provider keys encrypted with the user's DEK; vision via user-approved `view_images`
- **AI Agents**: per-user chat personas (system prompt + explicit tool allow-list) with three seeded built-ins; every mutating tool call pauses the turn for an approval card in the chat (optional per-chat "always allow")
- **Backup & Recovery**: Full backups with integrity verification and recovery keys

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Web Framework | FastAPI 0.129.0 |
| Server | Uvicorn 0.41.0 |
| Database | SQLite (sqlite3 module; SQLAlchemy engine + Alembic for schema migrations) |
| Templates | Jinja2 3.1.6 |
| Styling | Vanilla CSS |
| Frontend JS | Vanilla JavaScript (modular) |
| Image Processing | Pillow 12.1.1 |
| Video Processing | ffmpeg / ffprobe (external binary; gyan.dev full build) |
| Encryption | cryptography (AES-256-GCM, chunked streaming envelope) |
| Password Hashing | bcrypt |
| Hardware Keys | webauthn >=2.0.0 (FIDO2) |

## Project Structure

```
Synth-Gallery/
├── app/                          # Main application
│   ├── main.py                   # FastAPI entry point, lifespan management
│   ├── config.py                 # Configuration constants, environment variables
│   ├── database.py               # DB connections, Alembic migration runner, password hashing
│   ├── logging_config.py         # Logging setup (SYNTH_LOG_LEVEL / SYNTH_ENV)
│   ├── middleware.py             # AuthMiddleware, CSRFMiddleware, RateLimitMiddleware
│   ├── dependencies.py           # FastAPI dependencies (get_current_user, etc.)
│   ├── tags.py                   # Tag dictionary for photo suggestions
│   ├── application/              # Application services (business logic)
│   │   └── services/
│   │       ├── auth_service.py       # Authentication, sessions, DEK management
│   │       ├── folder_service.py     # Folder CRUD, tree operations
│   │       ├── permission_service.py # Access control logic
│   │       ├── item_service.py       # Item (photo/video/note) operations
│   │       ├── item_types.py         # Typed item-type registry (single source of truth)
│   │       ├── item_renderers.py     # Strategy renderers per item type
│   │       ├── album_service.py      # Album CRUD and operations
│   │       ├── tag_service.py        # Tag CRUD and bulk operations
│   │       ├── tag_implication_service.py # Tag implication inheritance
│   │       ├── tag_suggestion_service.py  # PMI-based tag suggestions
│   │       ├── ai_chat_service.py    # Chat orchestrator: agent loop, SSE, vision pause, tool confirmations
│   │       ├── ai_agent_service.py   # AI agents: CRUD, validation, built-in seeding
│   │       ├── ai_provider_service.py # Per-user LLM providers/models/settings
│   │       ├── ai_tools/             # Tool registry the chat model may call
│   │       └── user_settings_service.py # User preferences
│   ├── infrastructure/           # Infrastructure layer
│   │   ├── repositories/         # Repository pattern (DB operations)
│   │   │   ├── base.py               # Repository base class
│   │   │   ├── user_repository.py
│   │   │   ├── session_repository.py
│   │   │   ├── folder_repository.py
│   │   │   ├── permission_repository.py
│   │   │   ├── item_repository.py    # Polymorphic items (photos, videos, notes)
│   │   │   ├── item_media_repository.py  # Media-specific data
│   │   │   ├── item_text_repository.py   # Text-note metadata
│   │   │   ├── album_repository.py
│   │   │   ├── tags_repository.py
│   │   │   ├── tag_implication_repository.py
│   │   │   ├── tag_cooccurrence_repository.py
│   │   │   ├── tag_feedback_repository.py
│   │   │   ├── tag_mutex_repository.py
│   │   │   ├── ai_chat_repository.py      # Conversations + messages
│   │   │   ├── ai_agent_repository.py     # Agent definitions (prompt + tool allow-list)
│   │   │   ├── ai_chat_settings_repository.py # Active provider/model per user
│   │   │   ├── ai_model_repository.py     # Models of a provider
│   │   │   ├── ai_provider_repository.py  # Per-user LLM provider configs
│   │   │   └── webauthn_repository.py
│   │   ├── services/             # Infrastructure services
│   │   │   ├── encryption.py         # Chunked AES-256-GCM streaming envelope, DEK cache
│   │   │   ├── session_dek.py        # Per-session DEK cache helpers
│   │   │   ├── backup.py             # Backup/restore service + scheduler
│   │   │   ├── ffmpeg.py             # ffmpeg/ffprobe wrappers (probe + thumbnail)
│   │   │   ├── media.py              # Image processing (Pillow only)
│   │   │   ├── image_conversion.py   # Download-time image conversion (JXL/JPEG/PNG/WebP)
│   │   │   ├── jxl.py                # JXL detection/decoding helpers
│   │   │   ├── jxl_encoder.py        # Lossless JXL encoding via cjxl
│   │   │   ├── jxl_fallback_service.py # On-demand JPEG fallbacks for JXL
│   │   │   ├── metadata.py           # EXIF/metadata extraction
│   │   │   ├── thumbnail.py          # Thumbnail generation/regeneration
│   │   │   ├── audit_log.py          # Security event audit log
│   │   │   ├── rate_limiter.py       # Request rate limiting
│   │   │   ├── tag_stats_scheduler.py # Tag co-occurrence stats scheduler
│   │   │   ├── llm/                  # LLM clients (OpenAI-compatible, Anthropic, Gemini) + SSE
│   │   │   └── webauthn.py           # Hardware key support
│   │   └── storage/              # Storage abstraction layer
│   │       ├── base.py               # StorageInterface
│   │       ├── local_storage.py      # Filesystem backend
│   │       ├── s3_storage.py         # S3/MinIO backend
│   │       └── factory.py            # get_storage() factory
│   ├── routes/                   # API routes
│   │   ├── auth.py                   # Login/logout
│   │   ├── admin.py                  # Admin panel (users, backups, maintenance)
│   │   ├── admin_tags.py             # Admin tag operations (delete/remap/sanitize)
│   │   ├── ai_chat.py                # AI chat conversations, streamed turns, vision + tool decisions
│   │   ├── ai_agents.py              # AI agent CRUD + tools catalogue
│   │   ├── ai_provider_settings.py   # Per-user AI provider/model/settings endpoints
│   │   ├── folders.py                # Folder management
│   │   ├── tags.py                   # Tag management
│   │   ├── webauthn.py               # Hardware key registration/auth
│   │   ├── user_settings.py          # User profile settings
│   │   └── gallery/                  # Gallery routes
│   │       ├── main.py               # Main gallery view
│   │       ├── items.py              # Item (photo/video/note) operations
│   │       ├── files.py              # File serving
│   │       ├── uploads.py            # Upload handling
│   │       └── deps.py               # Gallery dependencies
│   ├── static/                   # Static assets
│   │   ├── style.css
│   │   └── js/                       # Classic-script IIFE modules
│   │       ├── core.js               # Core utilities
│   │       ├── init.js               # Initialization
│   │       ├── navigation.js         # Navigation
│   │       ├── upload.js             # Upload handling
│   │       ├── ai-agents-icons.js    # Agent avatar icon/color presets
│   │       ├── ai-chat.js            # AI chat panel (SSE streaming, tool cards, agent picker)
│   │       ├── settings-ai-agents.js # AI agent CRUD (settings tab)
│   │       ├── settings-ai.js        # AI provider settings tab logic
│   │       ├── tag-links.js          # [[tag]] wiki-link helpers for tag descriptions (Jest-tested)
│   │       └── gallery-*.js          # Gallery features
│   └── templates/                # Jinja2 templates
│       ├── base.html
│       ├── login.html
│       ├── gallery.html
│       ├── settings.html
│       ├── reset_password.html
│       ├── admin_users.html
│       ├── admin_backups.html
│       └── admin_maintenance.html
├── migrations/                   # Alembic migration environment
│   ├── env.py                    # Online migrations via app.database.get_engine()
│   ├── migration_utils.py        # rebuild_table / backup helpers (SQLite specifics)
│   └── versions/                 # Revisions 0001-0013
├── tests/                        # Test suite
│   ├── conftest.py               # pytest fixtures
│   ├── integration/              # Integration tests
│   ├── unit/                     # Unit tests
│   ├── js/                       # Jest frontend unit tests
│   ├── e2e/                      # Playwright E2E (optional)
│   └── manual/                   # Manual test checklist
├── uploads/                      # Uploaded files (server-side encrypted)
├── thumbnails/                   # Generated thumbnails (server-side encrypted)
├── fallbacks/                    # JXL JPEG-fallback cache (encrypted)
├── backups/                      # Backup storage
├── gallery.db                    # SQLite database
├── pyproject.toml                # Project configuration and dependencies (PEP 621)
├── package.json                  # Jest configuration (frontend unit tests)
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
- **Legacy format**: The old whole-file `[12B nonce][ciphertext+tag]` envelope is no longer readable — all stored files must be in SGE1 format (re-encrypted during the 2.0 development cycle)

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
- Cache policy: media answers carry `Cache-Control: private, max-age=3600` (blobs are immutable per item id); **notes are served with `no-store`** because they are rewritten in place under the same id (manual editor and the `write_note` tool) — a cached note would show the pre-edit body after reload
- The old plaintext-fallback branch was removed; all files are expected to be in SGE1 format

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
- `POST /api/items/batch-download` takes `{item_ids, album_ids, options}`; `options.format` selects `original` (default = as stored, no re-encode), `jxl`, `jpeg`, `png` or `webp` plus per-format settings (quality sliders, WebP lossless, PNG optimize, JXL effort override via `encode_to_lossless_jxl(effort=...)`)
- A selection resolving to a single file is served directly with `Content-Disposition: attachment` (no ZIP); two or more files produce a **streaming ZIP** (`zipstream-ng`): the archive is generated while it is sent to the client, with no server-side spool file
- The ZIP uses `ZIP_STORED` (media payloads are already compressed; DEFLATE would only burn CPU)
- Items are prepared (download → decrypt → convert) in parallel by a worker pool (`SYNTH_DOWNLOAD_WORKERS`, default 4) with a bounded look-ahead window, while the build thread writes the archive; the whole pipeline runs off the event loop (`iterate_in_threadpool`), so the server stays responsive during downloads
- Album items land in an `{AlbumName}/` subfolder (single-album downloads name the ZIP after the album); entry names are sanitized and deduplicated (`name (2).ext`)
- Conversion lives in `app/infrastructure/services/image_conversion.py`; stored JXL is decoded via djxl stdin/stdout (raw PNM pixels, alpha/16-bit fall back to PNG) and jxl→jpeg downloads use bit-exact `djxl -J` JPEG reconstruction when the container carries the original JPEG; targets are encoded via Pillow/cjxl; **videos always pass through unchanged** (streamed straight into the archive); failed conversions fall back to the original bytes with a warning
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
- `StorageInterface.get_random_access_reader(file_id, folder)` returns a seekable `RandomAccessReader` (read / seek / tell / close / `size`)
- **LocalStorage** provides full random access (backed by an open file handle), so HTTP Range serving for videos works
- **S3Storage** provides a buffered range-reader (fetches byte ranges via S3 GetObject); the buffer is mandatory — every small `read()` must not become a GET request
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

### 6b. Polymorphic Items

Items use a **polymorphic base table** with **per-type detail tables**, driven by a **typed item-type registry** and **Strategy renderers**:

```
items (base)                          item_media (detail: type='media')
├── id (PK, TEXT UUID)                ├── item_id (PK/FK → items.id)
├── type (TEXT) ◄── 'media' | 'note'  ├── media_type ('image'|'video')
├── folder_id (FK)                    ├── content_type, original_name
├── user_id (FK)                      ├── width, height, duration
├── uploaded_at                       ├── thumb_width, thumb_height
├── title, description                └── taken_at, file_size, png_text_chunks
└── updated_at
                                      item_texts (detail: type='note')
                                      ├── item_id (PK/FK → items.id)
                                      ├── content_type (text/plain, text/markdown, ...)
                                      ├── original_name, encoding
                                      └── char_count, line_count
```

- **`items.type`** → coarse polymorphic kind (`media`, `note`; future `audio`/`model`)
- **`item_media.media_type`** → sub-kind within media (`image` | `video`). A 3D model is not "a kind of photo", but a video *is* "a kind of media"
- **Typed registry** (`app/application/services/item_types.py`): `ITEM_TYPE_REGISTRY` maps each `items.type` to its detail table, renderer factory and allowed MIME set. Dispatch sites (file serving, upload validation, `ItemService` hydration) consult `get_item_type_spec()` / `is_known_item_type()` instead of hard-coding `'media'`. Adding a new type = a new detail table + a new renderer + one registry entry
- **Renderers** (`app/application/services/item_renderers.py`): `ItemRenderer` ABC + `MediaRenderer` + `NoteRenderer`. `render_gallery_item()` publishes `type`, `media_type`, `width`, `height`, `has_thumbnail`, `thumbnail_url`. The gallery frontend derives thumbnail URLs from the item id itself, so the URL fields are informational
- **Consolidated read model** (`ItemRepository.get_media_with_details`): the single `items JOIN item_media` query lives on the base repository; `ItemMediaRepository` no longer owns the JOIN
- **Text notes**: content is stored as an SGE1-encrypted file in `uploads/` (same as media); only metadata lives in `item_texts`. Legacy cp1251 text is decoded and stored as UTF-8

**Legacy `photo_*` purge:**
- Removed `can_access_photo`, `can_delete_photo`, `get_photo_count`, `get_standalone_photos` aliases
- API response keys renamed: `photo_count` → `item_count`, `cover_photo_id` → `cover_item_id`, request bodies `photo_ids` → `item_ids`
- File-serving path param renamed `photo_id` → `item_id` (URL is `/files/{item_id}`)

### 6c. Schema Migrations (Alembic)

All schema changes are Alembic revisions under `migrations/versions/` (0001 baseline → 0012 `ai_reasoning`). `init_db()` runs `command.upgrade(alembic_cfg, "head")` automatically at startup via `run_db_migrations()`; the Alembic env (`migrations/env.py`) reuses `app.database.get_engine()`, so `SYNTH_DB_PATH` and test path-patching apply to migrations too.

- Migrations run **without an enclosing transaction** — SQLite's implicit DDL commits and `PRAGMA foreign_keys` no-ops inside transactions are what the rebuild helpers rely on
- `migrations/migration_utils.py` provides `rebuild_table()` (SQLite cannot alter CHECK constraints or drop FK-referenced columns in place) and `backup_database()` (pre-migration safety copy). `rebuild_table` commits before toggling `PRAGMA foreign_keys`, otherwise `DROP TABLE` cascades into child rows (`album_items`, `item_media`)
- Revisions must introspect the live schema (`PRAGMA table_info`) when rebuilding legacy tables — production databases can carry relic columns that fresh databases never had (regression test: `tests/integration/test_migration_legacy_users.py`)
- Every revision implements `downgrade()` for rollback

Notable historical revisions:
- **0002**: dropped dead `item_media.storage_mode`/`filename` columns, added `CHECK (type IN ('media'))` and `CHECK (media_type IN ('image', 'video'))`, normalised legacy `media_type='3d'` rows to `'image'`
- **0003**: enforced `albums.name NOT NULL` (NULLs backfilled with `Untitled (id8)`); the album API validates names (`min_length=1`)
- **0004**: dropped the relic `users.password_salt` column
- **0005/0006**: added the `note` item type and its `item_texts` detail table
- **0009**: AI chat: per-user provider/model/settings/chat tables; dropped the legacy AI tagging queue (`ai_tagging_jobs`, `ai_api_keys`)
- **0010**: AI agents: `ai_agents` table (system prompt, icon, `tools_json` allow-list, built-ins) plus `ai_conversations.agent_id` and `ai_chat_settings.active_agent_id` (both FK-free, cleared app-side)
- **0011**: albums become media-only (notes removed; notes as effective covers caused "Access denied" thumbnails)
- **0012**: reasoning control: `ai_models.reasoning_support` (manual capability override, validated app-side) + `ai_chat_settings.reasoning_effort`; `recreate_table_without_column` now preserves composite PRIMARY KEYs (table-level constraint)
- **0013**: chat message tree: `ai_messages.parent_id` (previous message in the branch) + `ai_conversations.active_leaf_id` (leaf of the branch history replay walks); enables edit/retry branching (see 6d)

### 6d. AI Agents

Conversations can run with an **agent** — a per-user persona stored in
`ai_agents` (migration 0010): a system prompt, an avatar (preset icon +
color index) and an explicit tool allow-list (`tools_json`; empty = no
tools). `ai_conversations.agent_id` binds a conversation to an agent at
creation time and never changes afterwards; a dangling id falls back to
the default assistant (no agent = every tool, generic prompt).

- **Built-ins**: `tagger`, `librarian`, `curator` are seeded idempotently
  per user from `BUILTIN_AGENTS` (`app/application/services/ai_agent_service.py`)
  on every `GET /api/user/ai/agents` — prompt improvements roll out while
  row ids stay stable. Built-ins are read-only in the UI (duplicate to
  customize).
- **Tool filtering**: `_run_llm_loop` resolves the agent once per turn and
  advertises only `TOOL_SPECS` entries in the allow-list
  (`AiChatService._tool_specs`); the allow-list is enforced again at
  execution time (`_execute_tool_calls` answers off-list calls with an
  error result instead of executing).
- **System prompt**: `AiChatService._build_system_prompt(agent, allowed)`
  = agent prompt (or the generic gallery identity) + house rules, with
  tool-conditional hints (vision batches, audit, notes, metadata) included
  only when the corresponding tool is allowed.
- **Mutation approval**: every `ToolDef` carries a `category` (editor UI
  grouping) and a `confirmation` level (`none` / `always`). `always`
  tools (all mutations) pause the turn: the orchestrator stores a
  `ToolConfirmation` (plus the remaining batch calls), emits SSE
  `tool_confirm`, and the frontend shows an approval card. The decision
  endpoint (`POST /api/ai/chat/conversations/{id}/tools/{request_id}/decision`,
  body `{approved, always_allow}`) executes or denies the call and
  resumes the turn; `always_allow` remembers `(conversation_id, tool_name)`
  in-process for the session. Pending confirmations use the same TTL and
  replay-dropping pattern as vision requests.
- **Message attachments**: `SendMessageRequest` accepts `item_ids` and
  `album_ids`; the route expands each album to its member item ids via
  `AlbumService.list_album_item_ids` (visibility-checked — unknown or
  invisible albums answer 404 before streaming starts) and merges both
  lists deduped into the user message's `context` part. The composer
  chip attaches the gallery selection (`selectedPhotos` +
  `selectedAlbums`); messages render attachments as a count chip plus up
  to 8 square thumbnails (`/files/{id}/thumbnail`, cap + "+N" tile,
  unloadable thumbnails collapse into a placeholder tile).
- **Web access**: `fetch_url` (one public URL → readable text; HTML is
  reduced to text, JSON/XML passes through; long pages paged via
  `next_offset`) and `web_search` (keyless DuckDuckGo HTML scrape) live
  in `app/application/services/ai_tools/web.py` under category `web`.
  Both are `confirmation="always"` (data can leave via the URL/query)
  and push the blocking HTTP into a worker thread via
  `asyncio.to_thread` — network I/O must never touch the event loop.
  SSRF guard: http(s) only, the host must resolve to a public address,
  every redirect hop is re-validated, responses capped at 2 MB / 15 s.
  Tag research targets keyless booru APIs (danbooru `tags.json` /
  `wiki_pages/<tag>.json`, e621 `tags.json`) — hinted in the
  tool-conditional system-prompt block and the built-in prompts. Unit
  tests stub the HTTP seam (`web._http_request`) — tests never touch
  the network.
- **Tag descriptions**: tags carry Markdown descriptions
  (`tags.description`, cap `TAG_DESCRIPTION_MAX_CHARS`). The mutating
  `set_tag_descriptions` tool (category `tags`, confirmation `always`,
  Curator built-in) bulk-rewrites them all-or-nothing via
  `TagService`-style name resolution. Descriptions may cross-reference
  tags with `[[tag_name]]` wiki-links: preprocessing/parse helpers live in
  `app/static/js/tag-links.js`; tag descriptions are rendered with marked +
  DOMPurify by `tags-page.js` inside the in-gallery `#tags-view` (the old
  `/tags` template was deleted — see section 6i). Wiki-links navigate
  in-page via `selectTag`, and `/tags?tag=<name>` is a shareable deep link
  (302 redirects to `/?view=tags&tag=<name>`, the sidebar has a matching
  Tags link under the AI-chat toggle).
- **Endpoints**: `/api/user/ai/agents` (list + tool catalogue, create),
  `PUT/DELETE /api/user/ai/agents/{id}` (custom only — built-ins answer
  403), `POST /api/user/ai/agents/{id}/duplicate`,
  `PUT /api/user/ai/agents/active` (agent preselected for new chats,
  stored in `ai_chat_settings.active_agent_id`).
- **Frontend**: the chat header shows an agent chip whose dropdown starts
  a new draft with the chosen persona (switching never rewrites an open
  conversation, mirroring the Synth-music app); agent CRUD lives in
  Settings → AI Agents (`settings-ai-agents.js`); icon/color presets are
  shared between server (`AGENT_ICONS` / `AGENT_COLOR_COUNT`) and client
  (`app/static/js/ai-agents-icons.js`) and must stay in sync. A restored
  agent (from `active_agent_id` or the conversation) updates the welcome
  placeholder too, not just the header chip.
- **Mobile (<901px)**: the chat-list column overlays the panel and hides
  the header toggle, so the list header carries its own close button
  (`.ai-chat-list-close`, hidden on desktop) plus swipe gestures — swipe
  left on the list closes it, swipe right from the left edge of the chat
  area opens it (same pattern as the gallery sidebar); picking a chat or
  "New chat" closes the overlay. The vision slot is shared by the vision
  and approval cards: after a decision `renderToolConfirm` clears it
  itself — `renderVision`'s key-based early return skips the clear when
  it was already idle.

### 6e. Reasoning-effort control

Models differ wildly in how (and whether) their reasoning can be steered,
so the capability is a **per-model** property and the effort a **per-user**
preference:

- **Capability** (`ai_models.reasoning_support`, migration 0012; NULL =
  auto): `levels` (`reasoning_effort` — OpenAI/OpenRouter-style), `toggle`
  (`thinking: {type}` — Z.ai/GLM), `enable_toggle` (`enable_thinking` —
  Qwen3), `none` (explicitly no control). For the dedicated
  anthropic / google_gemini protocols the wire format is fixed by the
  protocol (thinking budget / `thinkingConfig`), so any non-`none` value
  resolves to `budget`. All heuristics and per-protocol payload mapping
  live in a single module: `app/infrastructure/services/llm/reasoning.py`
  (conservative by design — unknown model ids send nothing; strict servers
  that 400 on unknown fields stay safe; the Settings → AI model editor
  overrides the auto-detection per model and `fetch-models` merges keep
  the override).
- **Choice** (`ai_chat_settings.reasoning_effort`, NULL = provider
  default): set from the chat composer chip ("Thinking · Medium", styled
  like the attach chip; menu opens upward from the footer) via
  `PUT /api/user/ai/settings/reasoning` `{effort}`, validated against the
  active model's effective style. A stored choice that does not fit the
  active model degrades to the provider default at use time
  (`normalize_effort`) but is kept in the DB, so switching models back and
  forth restores it. `get_active_endpoint()` resolves
  `reasoning_style` + `reasoning` into the endpoint dict;
  `ChatRequest.reasoning_style` / `.reasoning` carry them to the clients,
  each of which maps to its wire format (Anthropic clamps the budget under
  an explicit `max_tokens` and pins `temperature=1` while thinking; Gemini
  3 uses `thinkingLevel` instead of a budget, and Pro cannot disable).

### 6f. Chat message tree (edit / retry / versions)

Messages form a tree (migration 0013): `ai_messages.parent_id` chains a
message to its predecessor inside one branch and
`ai_conversations.active_leaf_id` names the leaf of the branch that new
messages append to and that history replay walks.

- **Appending**: `AiChatRepository.add_message(..., parent_id=...)` uses a
  sentinel (`_AUTO_PARENT`) — omitting the argument chains onto the active
  leaf (legacy conversations: `MAX(id)`), passing `None` explicitly anchors
  a new branch root. The inserted row becomes the active leaf.
- **Reading**: `get_messages` walks parent links from the active leaf to
  the root (legacy `active_leaf_id` NULL = old "newest N by id" behavior);
  LLM replay and the GET history endpoint share it.
- **Edit/retry**: `POST .../messages` with `edit_of_message_id` replaces an
  earlier user message as a NEW sibling version (branching off the
  original's parent — the old branch is kept, never deleted).
  `check_edit_target` prevalidates in the route (404 before the stream);
  paused vision/confirmation requests of the conversation are dropped
  (`_drop_pending_for_conversation`) — resolving them later would append to
  the abandoned branch.
- **Version switch**: `POST .../branch` body `{message_id}` activates the
  branch through a user message (`deepest_leaf` follows the newest-child
  chain down); 409 while a turn is running. Pending decisions are dropped
  the same way.
- **Version metadata**: `GET .../messages` enriches every user message with
  `parent_id`, `sibling_ids` (the whole version group), and
  `version`/`version_count` so the frontend renders the ‹k/n› chips without
  extra round-trips.
- **Frontend** (`ai-chat.js`): stored user messages get hover Edit/Retry
  actions (hidden while streaming; always visible on touch) and an inline
  edit box (Enter = save, Escape = cancel). Edit/retry POST
  `edit_of_message_id` and optimistic-echo the new message; every finished
  stream (or branch switch) triggers a history refetch that binds server
  message ids and shows the active branch. Message signatures include the
  editing/version state so the incremental renderer swaps the action bar.

### 6g. Chat tool ids and result limits

- **Short ids**: tool outputs emit 12-char id prefixes (`short_id()`,
  `ID_SHORT_LEN`) instead of full 36-char UUIDs to save context. Every
  id-accepting tool resolves a full id or a unique prefix via
  `_resolve_item_id` / `_resolve_folder_id` / `_resolve_album_id`
  (`registry.py`): exact match first, then `LIKE 'prefix%'` scoped to the
  visible set (owner or viewer/editor folder permission); an ambiguous
  prefix raises `ToolError` with a candidate list. `_get_owned_item(db, ...)`
  resolves and callers re-read the full id from the returned row — never
  keep using the raw short argument downstream. Tag ids are small integers
  and are never shortened. The system prompt tells the model to pass the
  prefixes back as-is.
- **Album contents**: `list_album_items` (category `library`, Librarian)
  pages one album's items (`album_id` + `offset`/`limit`, `total` +
  `next_offset`); visibility = the album's folder owner or viewer/editor.
- **Audit scope**: `audit_library` takes `include_subfolders` (default
  true = the folder's whole subtree; false = the folder itself only).
  Album items always appear in audits (`album_items` is a junction table;
  `items.folder_id` never changes) — regression-tested.
- **Result size**: `_truncate` caps one tool result at
  `config.TOOL_RESULT_LIMIT` (env `SYNTH_TOOL_RESULT_LIMIT`, default
  16000) BEFORE persisting, so truncation is visible in the stored
  history. `list_folder` pages its items with `offset` (max limit 1000,
  compact entries without the constant `type` field).
- **Suggestion feedback**: `record_tag_feedback` (category `tags`,
  confirmation `always`, Tagger + Curator) batch-records
  accepted/rejected verdicts on `get_tag_suggestions` output (accepting
  does NOT add the tag — that is `add_item_tags`). A rejection is a
  per-item blocklist: `TagSuggestionService.get_suggestions_for_item`
  excludes tags rejected for that item forever, and the gallery panel
  (`gallery-tags.js` `rejectSuggestion`) refetches right after a reject
  so the list refills with the next-best candidates instead of
  shrinking.
- **grep_library**: grep-like full-text search over the library's text data.
  Sources (multi-select, default all six): item `titles`, item `descriptions`,
  `notes` bodies (decrypted with the session DEK), `tags` (names + descriptions,
  global dictionary — never folder-scoped), media `metadata` (PNG text chunk
  keys and values from `item_media.png_text_chunks`), original `filenames`
  (`item_media.original_name`). Matching is uniform Python `re` over fetched
  rows (substring escaped by default, case-insensitive; `regex=true` for Python
  regex syntax; `case_sensitive=true` opt-in). Results carry short context
  snippets (`context_chars`, max `_GREP_CONTEXT_MAX_CHARS` / 200) and per-source
  paging (`limit`/`offset` + `next_offset`); item/folder ids are 12-char prefixes
  like everywhere else. `folder_id` scopes item sources to a folder subtree
  (owner-only resolve, like `audit_library`); `include_subfolders=false` restricts
  to the folder itself. Notes scanning is capped: the `_GREP_MAX_NOTES` / 300 most
  recent eligible notes, size estimated as `char_count * 4` bytes against a
  `_GREP_NOTE_MAX_BYTES` / 512 KiB cap (oversized/capped/decrypt-failed counts
  reported in a `notes` warnings list); without a DEK the notes source answers
  empty with a warning. Registered in the `librarian` and `curator` built-in agent
  allow-lists.
- **Note tools**: `read_note` reads a note paged (decrypt + offset/max_chars),
  `create_note` creates one. Existing notes change through `write_note`
  (`replace` = full rewrite, `append` = add to the end) or `edit_note`
  (targeted exact-string swap: first occurrence of `old_string` becomes
  `new_string`, every occurrence with `replace_all=true`; fails when the
  fragment is not found or matches more than once without `replace_all`,
  so the model must copy the fragment verbatim from `read_note` output).
  `edit_note` revalidates the patched result against the same
  NUL/size/non-empty rules as uploads before re-encrypting it. All three
  mutation tools are category `notes`, confirmation `always`, and their
  acks carry only counters (`replacements`/`char_count`/`line_count`),
  never content. Built-in agents carry no note mutations (Librarian is
  read-only: `read_note` only) — custom agents list them explicitly.

### 6h. AI Memory

Per-user opt-in memory (migration `0014`: `ai_chat_settings.memory_enabled`
+ `memory_folder_id`, FK-free). A private folder holds the `MEMORY.md`
index note (`text/markdown`, always embedded into the system prompt while
memory is enabled) and section notes `{name}.md` with frontmatter
`name`/`description`/`when_to_use`. Everything is regular notes — the user
can view and edit them like any other note. Shared logic lives in
`app/application/services/ai_memory_service.py` (imports only
repositories/encryption/storage — importing `ai_tools` there would be
circular).

- **Index format**: `## Summary` (1-2 sentences about the user, written
  only by `set_memory_summary`) and `## Sections`, one entry block per
  section: `- **name** — description` plus an indented
  `  Use when: {scenario}` continuation line. Legacy single-line entries
  (pre-when_to_use) are replaced wholesale on the next upsert. The index
  is tool-managed: writing tools refuse `name="MEMORY"` with guidance
  (`_INDEX_WRITE_ERROR` in `ai_tools/registry.py`) — only
  `set_memory_summary` and the section tools touch it. The model is told
  to read the index as the map: entries are deliberately brief, section
  notes hold the details, and a section is read when its "Use when:"
  scenario matches the task.
- **Settings**: `PUT /api/user/ai/settings/memory` `{enabled, folder_id?}`
  (`AiProviderService.update_memory_settings`). Enabling requires a folder
  the user owns with `get_share_status() == 'private'` (400 otherwise);
  the folder falls back to the previously stored one on re-enable;
  disabling keeps the folder. Every enable (re)creates `MEMORY.md` via
  `ensure_index_note` (self-healing when the user deleted it).
- **Tools** (`MEMORY_TOOL_NAMES` in `ai_tools/registry.py`, category
  `memory`, all `confirmation="none"`): `read_memory(name, offset?,
  max_chars?)` — paged like `read_note`, `name="MEMORY"` reads the full
  index, result meta carries `when_to_use`;
  `create_memory_section(name, description, when_to_use, content?)` —
  writes `{name}.md` (frontmatter includes `when_to_use:`) and upserts
  the entry block (bullet + Use-when line) under `## Sections` of the
  index; `when_to_use` is required — the model must name the situations
  the section applies to; `write_memory(name, content,
  mode=replace|append, description?, when_to_use?)` — replace changes
  the body only (frontmatter preserved), append adds to the end; passing
  `description`/`when_to_use` refreshes the index entry (the index
  itself cannot be targeted); `edit_memory(name, old_string, new_string,
  replace_all?)` — patches the full text including the frontmatter
  (description/when_to_use fixes allowed, renaming the `name:` line
  rejected); `delete_memory_section(name)` — deletes the section note
  (blob + rows, `item_texts` cascades) and removes its entry block from
  the index; `set_memory_summary(summary)` — replaces the `## Summary`
  block (recreates the heading/title when hand-stripped). Names match
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`,
  are unique case-insensitively in the folder; `MEMORY` is reserved.
  Resolution: `{name}.md` title/original_name first, frontmatter `name:`
  as fallback; duplicates answer "ambiguous".
- **Orchestrator** (`AiChatService._effective_tools`): memory tools are
  global — while memory is enabled they join every allow-list (restricted
  agents included) and are enforced again at execution time; while
  disabled they are stripped from the default assistant's "every tool"
  set. `_memory_prompt_block` reads the index once per turn and injects
  instructions + the index text + the memory folder's 12-char id into the
  system prompt (via `_build_system_prompt(..., memory_block=...)`).
- **Agent editor**: memory tools are hidden from the tools catalogue
  (`_tools_catalogue` in `app/routes/ai_agents.py`) and rejected in
  allow-list validation (`ai_agent_service._validate_fields`) — they are
  managed by the memory setting, not by agents.
- **Guards**: `POST /api/folders/{folder_id}/permissions` answers 403
  when the folder is an ACTIVE memory folder of any user
  (`AiChatSettingsRepository.memory_folder_user_ids`);
  `DELETE /api/folders/{folder_id}` calls `clear_memory_folder(folder_id)`
  so no settings row keeps pointing at the deleted folder
  (`get_memory_config` also revalidates ownership at read time).
- **Frontend**: Settings → AI Memory card (`settings.html` +
  `settings-ai.js`): checkbox + folder `<select>` (GET /api/folders,
  client-side filter `permission === 'owner' && share_status ===
  'private'`; the stored folder stays selectable but the server rejects
  saving it if it became shared).

### 6i. In-gallery Tags view

The standalone `/tags` page no longer exists — tag management is rendered
inside the gallery main area as an SPA view (like folder navigation):

- **Hidden container**: `app/templates/gallery.html` has a hidden `<div id="tags-view">` with the full tag management UI (toolbar, `#tag-list`, `#pagination-info`, `#detail-panel`, and three admin modals `#categories-modal` / `#remap-tag-modal` / `#new-tag-modal` wrapped in `{% if user and user.is_admin %}`). It also sets `window.__isAdmin` and loads `tag-links.js` + `tags-page.js` at the end of the content block.
- **SPA switching**: `app/static/js/navigation.js` provides `window.showTagsView(pushState=true|'replace'|false)`, `window.hideTagsView()`, and `window.isTagsViewVisible()`. `navigateToFolder` / `navigateToDefaultFolder` hide the tags view on entry; a delegated `document` click handler intercepts `a[href$="view=tags"]` links (sidebar, details panel) on the gallery page only. The `popstate` handler routes on `{tagsView: true}` vs `{folderId}` / no-state.
- **URL**: `history.replaceState({tagsView: true}, ...)` writes `?view=tags[&tag=<name>]`. Deep links resolve via `app/routes/admin_tags.py`: `GET /tags` is a 302 redirect to `/?view=tags` (preserves `?tag=<name>`, URL-encoded); old template `app/templates/tags.html` was deleted.
- **Tag categories**: `app/static/js/tags-page.js` init is now a lazy idempotent `window.initTagsPage()` (guarded by `window.__tagsPageInited`). Categories are fetched from `GET /api/tag-categories` instead of the server-rendered `window.__categories`; they populate `#new-tag-category` / `#edit-category` via `updateCategorySelectOptions`. The legacy auto-init IIFE runs only when `#tags-view` is absent (old page mode, kept for safety). The whole file is a classic-script IIFE: only `initTagsPage`, `navigateToTagByName` and the functions used by generated inline `onclick` handlers are exported on `window` — its internal `csrfFetch`/`getCsrfToken` must never shadow the `core.js` globals (they prefix `BASE_URL`, so shadowing made every gallery delete/move/copy hit `/synth/synth/api/...` and 404; regression test `tests/js/tags-page-isolation.test.js`).
- **CSS**: All tags selectors are scoped under `#tags-view` in `app/static/style.css` (section comment "Tags view (in-gallery, #tags-view)"); the old inline `<style>` block was removed.
- **Scripts loaded in gallery.html**: `tag-links.js` (`[[tag]]` wiki-link helpers) and `tags-page.js` (tag management SPA logic) are appended at the end of the content block in `gallery.html`.
- **Frontend deep link**: `app/static/js/init.js` handles `/?view=tags[&tag=<name>]` on DOMContentLoaded — calls `showTagsView('replace')`, then `navigateToTagByName`. Sidebar and admin-nav links point to `{{ base_url }}/?view=tags`; on the gallery page the click is intercepted for SPA switching, elsewhere it's a normal navigation.
- **Sidebar highlight**: the sidebar Tags link (`#tags-view-toggle` in `base.html`, same `.sidebar-ai-btn.active` styling as the AI Chat toggle) lights up in `showTagsView` / goes dark in `hideTagsView` (`navigation.js` `syncTagsSidebarHighlight`); folder items lose their `.active` while the tags view is open. `navigateToFolder` keeps the stale gallery + folder-header hidden (`display: none`) until `renderFolderContent` runs — restoring them eagerly flashed the previous folder's content while the fetch was in flight (Jest regression test `tests/js/tags-view-highlight.test.js`).

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

### JavaScript Unit Tests (Jest)

Frontend pure helpers (date parsing, masonry math, URL building, search
query parsing) are unit-tested with Jest + jsdom under `tests/js/`:

```bash
npm install   # once
npm test      # or: npx jest
```

The static JS files are classic-script IIFE modules; testable helpers are
exported through a CommonJS guard (`if (typeof module !== 'undefined' && module.exports)`)
that is inert in the browser. When you add logic to a gallery module,
extract the pure part and cover it with a Jest test.

### Test Structure

- `conftest.py`: Contains fixtures for isolated test environments
- `integration/`: Integration tests for routes and workflows
- `unit/`: Unit tests for individual components
- `js/`: Jest frontend unit tests (run with `npm test`)

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
| `SYNTH_EXTERNAL_HOST` | Public origin for shared/copy links | "" (current origin) |
| `SYNTH_ENV` | `production` reduces default log verbosity | `development` |
| `SYNTH_LOG_LEVEL` | Logging level (DEBUG, INFO, ...) | `INFO` |
| `COOKIE_SECURE` | Secure flag on `__Host-` session/CSRF cookies; set `false` only for HTTP dev | `true` |
| `WEBAUTHN_RP_NAME` | WebAuthn display name | "Synth Gallery" |
| `BACKUP_PATH` | Backup directory path | `./backups` |
| `BACKUP_SCHEDULE` | `daily`, `weekly`, or `disabled` | `daily` |
| `BACKUP_ROTATION_COUNT` | Number of backups to keep | 5 |
| `SYNTH_BACKUP_KEY` | Passphrase for encrypted backups | None (backups unencrypted) |
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `STORAGE_BASE_PATH` | Override local storage root | `SYNTH_UPLOADS_DIR`-derived |
| `S3_BUCKET` | S3 bucket name (required for s3) | - |
| `S3_REGION` | AWS region | `us-east-1` |
| `S3_ENDPOINT` | Custom endpoint (for MinIO) | - |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | S3 credentials | - |
| `S3_USE_SSL` | SSL for S3 connections | `true` |
| `USE_JXL` | Transcode new image uploads to lossless JPEG XL | `false` |
| `JXL_TOOL_DIR` | Directory containing `cjxl`/`djxl`; overrides PATH lookup | - |
| `JXL_FALLBACK_QUALITY` | JPEG quality for generated fallbacks | `85` |
| `JXL_LOSSLESS_TRANSCODE_JPEG` | Use lossless JPEG transcode for JPEG sources | `true` |
| `JXL_EFFORT` | Encoder effort: 1 (fast/large) to 9 (slow/small) | `7` |
| `JXL_THREADS` | Encoder threads: -1 auto, 0 single-threaded | `-1` |
| `JXL_PROGRESSIVE_AC` | Enable `--progressive_ac` for perceived loading speed | `true` |
| `JXL_QPROGRESSIVE_AC` | Enable `--qprogressive_ac` for perceived loading speed | `true` |
| `JXL_PROGRESSIVE_DC` | Extra low-resolution pass (`--progressive_dc`), `0` disables | `1` |
| `SYNTH_ENCRYPTION_CHUNK_SIZE` | Plaintext chunk size for the chunked AEAD envelope (bytes) | `1048576` (1 MiB) |
| `SYNTH_DOWNLOAD_WORKERS` | Worker threads preparing batch-download items in parallel | `4` |
| `SYNTH_TEXT_MAX_SIZE` | Max text-note upload size (bytes) | `10485760` (10 MiB) |
| `SYNTH_TOOL_RESULT_LIMIT` | Max chars of one AI chat tool result (stored/replayed truncated) | `16000` |
| `FFMPEG_TOOL_DIR` | Directory containing `ffmpeg`/`ffprobe` binaries; overrides PATH lookup | - |
| `TAG_STATS_SCHEDULE` | Tag co-occurrence stats: `daily`, `weekly`, or `disabled` | `weekly` |
| `TAG_STATS_HOUR` | Hour (0-23) when tag stats run | `3` |

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

1. **HTTPS Required**: WebAuthn and `__Host-` secure cookies require HTTPS (or localhost for development)
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

1. Create a new Alembic revision in `migrations/versions/` (next sequential number, `down_revision` pointing at the previous head)
2. Use `rebuild_table()` from `migrations/migration_utils.py` for CHECK/column changes (introspect the live schema with `PRAGMA table_info` — production databases may carry relic columns)
3. Implement `downgrade()` for rollback
4. Update relevant repository methods

## Troubleshooting

### "No module named 'app'"
Run from project root, not from app directory.

### Database locked errors
Ensure you're using `create_connection()` and closing properly.

### Session/DEK not persisting
Check cookie settings and DEK cache TTL.

### WebAuthn not working
Must use HTTPS or localhost (secure-context requirement).

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
