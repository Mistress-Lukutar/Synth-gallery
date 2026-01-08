# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Photo gallery web application with AI tagging support. Built with Python 3.11, FastAPI, SQLite, and Jinja2 templates.

## Development Commands

**Run with Docker (recommended):**
```bash
docker-compose up --build
```

**Run directly for development:**
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Environment variables:**
```bash
SYNTH_AI_API_KEY=your-secret-key  # Required for AI service API

# WebAuthn configuration (for hardware key auth)
WEBAUTHN_RP_NAME=Synth Gallery    # Display name shown during registration
```

Access at http://localhost:8000

## Architecture

```
app/
├── main.py              # FastAPI app entry point, router registration
├── config.py            # Constants, paths, environment variables
├── middleware.py        # AuthMiddleware, CSRFMiddleware
├── dependencies.py      # Shared dependencies (get_current_user, verify_api_key)
├── database.py          # SQLite connection, schema, queries, access control
├── routes/
│   ├── auth.py          # /login, /logout
│   ├── gallery.py       # /, /uploads, /thumbnails, /upload, /upload-album
│   ├── folders.py       # /api/folders/*, /api/users/search
│   ├── tags.py          # /api/tag-*, /api/photos/{id}/tag, /api/photos/search
│   ├── api.py           # /api/ai/* (AI service endpoints, API key protected)
│   └── webauthn.py      # /api/webauthn/*, /settings (hardware key auth)
├── services/
│   ├── media.py         # Thumbnail creation for images and videos
│   └── webauthn.py      # WebAuthn/FIDO2 registration and authentication
├── templates/           # Jinja2 templates (base.html, gallery.html, login.html)
└── static/              # CSS styling (dark theme)
```

**Data directories** (created at runtime):
- `uploads/` - Original uploaded photos/videos
- `thumbnails/` - Auto-generated 400x400 thumbnails
- `gallery.db` - SQLite database

### Key Routes

| Route | Method | Auth | Purpose |
|-------|--------|------|---------|
| `/` | GET | Session | Main gallery grid view |
| `/login` | GET/POST | Public | Authentication |
| `/upload` | POST | Session+CSRF | Upload single photo/video |
| `/upload-album` | POST | Session+CSRF | Upload multiple files as album |
| `/api/folders` | GET/POST | Session | Folder management |
| `/api/folders/{id}/permissions` | * | Session | Folder sharing |
| `/api/photos/{id}/tag` | POST/DELETE | Session+CSRF | Tag management |
| `/api/ai/photos/untagged` | GET | API Key | List untagged photos |
| `/api/ai/photos/{id}/tags` | POST | API Key | Set tags from AI service |
| `/settings` | GET | Session | User settings page |
| `/api/webauthn/register/*` | GET/POST | Session | Hardware key registration |
| `/api/webauthn/authenticate/*` | GET/POST | Public | Hardware key login |
| `/api/webauthn/credentials` | GET/DELETE/PATCH | Session | Manage registered keys |

### Security

- **Authentication**: Session-based with HTTP-only cookies, or WebAuthn hardware keys
- **Password hashing**: bcrypt via passlib (supports legacy SHA-256 migration)
- **WebAuthn/FIDO2**: Hardware key support for passwordless login (YubiKey, etc.)
- **CSRF protection**: Token in meta tag + X-CSRF-Token header for mutating requests
- **AI API**: Separate `/api/ai/*` endpoints protected by X-API-Key header

### Database Schema

- **users**: id, username, password_hash, password_salt, display_name, default_folder_id
- **sessions**: id, user_id, created_at, expires_at
- **webauthn_credentials**: id, user_id, credential_id, public_key, sign_count, name, created_at, encrypted_dek
- **folders**: id, name, parent_id, user_id
- **folder_permissions**: id, folder_id, user_id, permission (viewer/editor), granted_by
- **albums**: id, name, folder_id, user_id, created_at
- **photos**: id, filename, original_name, uploaded_at, ai_processed, album_id, position, media_type, folder_id, user_id
- **tags**: id, photo_id, tag, category_id, confidence
- **tag_categories**: id, name, color
- **tag_presets**: id, name, category_id

## Image Processing

Photos/videos are uploaded with UUID-based filenames. Thumbnails (400x400, JPEG quality 85) are generated automatically using Pillow for images and OpenCV for video first frames. If thumbnail creation fails, the upload is rolled back.

## AI Integration

The AI service endpoints are now at `/api/ai/*` and require API key authentication:

```bash
# Get untagged photos
curl -H "X-API-Key: $SYNTH_AI_API_KEY" http://localhost:8000/api/ai/photos/untagged

# Set tags for a photo
curl -X POST -H "X-API-Key: $SYNTH_AI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '["tag1", "tag2"]' \
     http://localhost:8000/api/ai/photos/{id}/tags

# Get statistics
curl -H "X-API-Key: $SYNTH_AI_API_KEY" http://localhost:8000/api/ai/stats
```

## User Management

Use the CLI tool to manage users:
```bash
python manage_users.py list
python manage_users.py add <username> <password> <display_name>
python manage_users.py delete <username>
python manage_users.py passwd <username> <new_password>
```

## Backup System

Full backups include the database and all encrypted media files:
```bash
python manage_users.py backup              # Create full backup
python manage_users.py backup-list         # List all backups
python manage_users.py verify <filename>   # Verify backup integrity
python manage_users.py restore <filename>  # Restore from backup
```

**Environment variables:**
- `BACKUP_PATH` - Directory for storing backups (default: `backups/`)
- `BACKUP_SCHEDULE` - Automatic backup schedule: `daily`, `weekly`, or `disabled`
- `BACKUP_ROTATION_COUNT` - Number of backups to keep (default: 5)

Admin UI available at `/admin/backups`.

## Recovery Key

Generate a recovery key to recover access if password is lost:
```bash
python manage_users.py recovery-key <username> <password>  # Shows one-time key
python manage_users.py recover <username> <recovery_key>   # Reset password
```

**Important:** The recovery key is shown only once. Store it securely!

## Hardware Key Authentication

WebAuthn/FIDO2 support allows users to login with hardware security keys (YubiKey, etc.):

1. User logs in with password, goes to `/settings`
2. Registers hardware key with a name (e.g., "YubiKey 5")
3. On future logins, enters username and clicks "Sign in with Hardware Key"
4. Browser prompts for key touch, user is authenticated

Keys are linked to the user's encryption key (DEK), so hardware key login also enables file decryption without password entry.

**Note:** Credentials are bound to the domain where they were registered. A key registered on `localhost:8008` will not work on `ggwp.isgood.host`. Register separate keys for each access method if needed.
