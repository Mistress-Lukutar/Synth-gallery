# Synth

Personal media vault with server-side encryption, hardware key authentication, and multi-user support.

## Key Features

### Security
- **Server-side Encryption** — AES-256-GCM chunked envelope for all uploaded files
- **Hardware Key Login** — WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Recovery Keys** — Generate backup keys to recover access if password is lost

### Storage & Organization
- **Folder Hierarchy** — Organize content in nested folders
- **Albums** — Group related media with drag-and-drop reordering
- **Sharing** — Share folders with other users (Viewer/Editor permissions)
- **Tags & Search** — Categorize and find content quickly
- **Text Notes** — Store txt/md/json/csv/yaml alongside media

### Backup & Recovery
- **Full Backups** — ZIP archives with database + encrypted files
- **Automatic Scheduling** — Daily/weekly backups with rotation
- **Integrity Verification** — SHA-256 checksums for all files

### Media Support
- Images: JPEG, PNG, GIF, WebP (incl. animated), JPEG XL (optional lossless storage)
- Videos: MP4, WebM, MKV
- Automatic thumbnail generation (ffmpeg for video)
- EXIF/metadata extraction, PNG text chunk editing

## Quick Start

### Windows (Recommended)

```bash
Start.bat
```

The app runs on http://localhost:8008/synth/ by default (port 8008, base path `synth` — both editable in `Start.bat`).

### Local Development

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

### Requirements

- Python 3.11+
- ffmpeg/ffprobe on `PATH` for video probing and thumbnails (e.g. `winget install Gyan.FFmpeg`)
- Optional: libjxl binaries (`cjxl`/`djxl`) for JPEG XL storage — `Start.bat` downloads them automatically

## First Run

On first startup, if no users exist, a temporary admin account is created:
- **Username:** admin
- **Password:** admin

**Important:** Log in with these credentials, then immediately create a new admin user and delete the temporary account.

## User Management

User management is available through the web UI at `/admin/users`:
- Create, edit, delete users
- Grant/revoke admin rights
- View user list

Profile settings (change password, recovery key, display name) are available at `/settings`.

## Backup & Restore

Admin UI available at `/admin/backups`:
- Create full backups (database + media files)
- Schedule automatic backups (daily/weekly)
- Verify backup integrity
- Download and restore from backups

## Hardware Key Setup

1. Log in with your password
2. Go to Settings (gear icon)
3. Add a hardware key (YubiKey, etc.)
4. Future logins: enter username → click "Sign in with Hardware Key"

Note: Keys are bound to the domain where registered. Register separate keys for each access method (localhost, VPN, public domain).

## Environment Variables

```bash
# Paths
SYNTH_DB_PATH=./gallery.db       # SQLite database location
SYNTH_UPLOADS_DIR=./uploads      # Uploaded files
SYNTH_THUMBNAILS_DIR=./thumbnails
SYNTH_FALLBACKS_DIR=./fallbacks  # JXL JPEG-fallback cache

# Server
SYNTH_BASE_URL=synth             # Base URL subpath (no slashes)
SYNTH_EXTERNAL_HOST=             # Public origin for shared links
SYNTH_ENV=production             # development enables extra logging
SYNTH_LOG_LEVEL=INFO
COOKIE_SECURE=true               # __Host- cookies behind HTTPS

# WebAuthn
WEBAUTHN_RP_NAME=Synth Gallery

# Backup
BACKUP_PATH=./backups
BACKUP_SCHEDULE=daily            # daily, weekly, or disabled
BACKUP_ROTATION_COUNT=5
SYNTH_BACKUP_KEY=                # Optional backup encryption key

# Storage backend
STORAGE_BACKEND=local            # local or s3
STORAGE_BASE_PATH=               # Override local storage root
S3_BUCKET=your-bucket
S3_REGION=us-east-1
S3_ENDPOINT=https://s3.amazonaws.com  # for MinIO/custom endpoints
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_USE_SSL=true

# JPEG XL (experimental lossless image storage)
USE_JXL=false
JXL_TOOL_DIR=                    # Directory containing cjxl/djxl
JXL_EFFORT=7                     # 1 (fast) to 9 (small)
JXL_THREADS=-1                   # -1 auto, 0 single-threaded
JXL_FALLBACK_QUALITY=85
JXL_LOSSLESS_TRANSCODE_JPEG=true
JXL_PROGRESSIVE_AC=true
JXL_QPROGRESSIVE_AC=true
JXL_PROGRESSIVE_DC=1             # 0 disables

# Encryption / uploads
SYNTH_ENCRYPTION_CHUNK_SIZE=1048576  # SGE1 chunk size in bytes
SYNTH_TEXT_MAX_SIZE=                 # Max text-note size in bytes

# Tools
FFMPEG_TOOL_DIR=                 # Directory containing ffmpeg/ffprobe

# Tag statistics scheduler
TAG_STATS_SCHEDULE=daily
TAG_STATS_HOUR=3
```

## Security Model

| Layer          | Protection                                      |
|----------------|-------------------------------------------------|
| Files at rest  | AES-256-GCM chunked envelope, per-user keys     |
| Password       | bcrypt + PBKDF2-SHA256 key derivation           |
| Sessions       | HTTP-only cookies, 7-day expiry                 |
| API            | CSRF tokens, rate limiting, security headers    |
| Login          | Password or WebAuthn hardware keys              |

## Tech Stack

- Python 3.11+ / FastAPI
- SQLite (SQLAlchemy Core + Alembic migrations)
- Jinja2 templates, vanilla JS/CSS
- Pillow (images) / ffmpeg (video)
- cryptography (AES-256-GCM)
- bcrypt (password hashing)
- py_webauthn (FIDO2)

## License

MIT
