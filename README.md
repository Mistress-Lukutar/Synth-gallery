# Synth

Local gallery for storing and organizing AI-generated images with multi-user support and folder sharing.

## Features

- **User Authentication** — Session-based auth with bcrypt password hashing
- **Folder System** — Hierarchical folder structure for organizing content
- **Sharing & Permissions** — Share folders with other users as Viewer (read-only) or Editor (can upload/delete)
- **Media Support** — Images (jpg, png, gif, webp) and videos (mp4, webm)
- **Albums** — Group multiple files into albums
- **Tag System** — Categories (Subject, Location, Mood, Style, Event, Other) with presets
- **Tag Search** — Autocomplete search across your accessible content
- **AI Tags** — Auto-generate tags for photos (simulation mode + external AI service API)
- **Batch Operations** — Delete or tag multiple items at once
- **Dark Theme** — Modern dark UI
- **Security** — CSRF protection, API key authentication for external services

## Quick Start

### Docker (recommended)

```bash
docker-compose up --build
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

### Environment Variables

```bash
SYNTH_AI_API_KEY=your-secret-key  # Required for external AI service API
```

### User Management

Create users via CLI:

```bash
python manage_users.py add <username> <password> [--display-name "Display Name"]
python manage_users.py list
python manage_users.py delete <username>
python manage_users.py passwd <username> <new_password>
```

## Folder Permissions

| Role | View | Upload | Delete Content | Manage Sharing | Delete Folder |
|------|------|--------|----------------|----------------|---------------|
| Owner | ✓ | ✓ | ✓ | ✓ | ✓ |
| Editor | ✓ | ✓ | Own content | ✗ | ✗ |
| Viewer | ✓ | ✗ | ✗ | ✗ | ✗ |

### Visual Folder Indicators

Folders in the sidebar are color-coded:

**My Folders:**
- Gray (lock icon) — Private, no one else has access
- Green border (eye icon) — Shared with viewers only
- Orange border (arrow icon) — Shared with editors

**Shared with me:**
- Green background — I have viewer access
- Orange background — I have editor access

## Project Structure

```
app/
├── main.py              # FastAPI app entry point
├── config.py            # Constants, paths, environment variables
├── middleware.py        # Auth and CSRF middleware
├── dependencies.py      # Shared dependencies
├── database.py          # SQLite schema, queries, access control
├── routes/
│   ├── auth.py          # Login/logout
│   ├── gallery.py       # Main gallery, uploads
│   ├── folders.py       # Folder management
│   ├── tags.py          # Tag management
│   └── api.py           # External AI service API
├── services/
│   └── media.py         # Thumbnail generation
├── templates/           # Jinja2 templates
└── static/              # CSS and icons

uploads/                 # Original files (created automatically)
thumbnails/              # 400x400 previews (created automatically)
gallery.db               # SQLite database
manage_users.py          # CLI for user management
```

## API

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/login` | Public | Login page |
| POST | `/login` | Public+CSRF | Authenticate user |
| GET | `/logout` | Session | Logout |

### Folders

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/folders` | Session | List user's folders and shared folders |
| POST | `/api/folders` | Session+CSRF | Create folder |
| PUT | `/api/folders/{id}` | Session+CSRF | Update folder |
| DELETE | `/api/folders/{id}` | Session+CSRF | Delete folder (owner only) |

### Folder Permissions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/folders/{id}/permissions` | Session | List folder permissions |
| POST | `/api/folders/{id}/permissions` | Session+CSRF | Add user permission |
| PUT | `/api/folders/{id}/permissions/{user_id}` | Session+CSRF | Update permission |
| DELETE | `/api/folders/{id}/permissions/{user_id}` | Session+CSRF | Remove permission |
| GET | `/api/users/search?q=` | Session | Search users for sharing |

### Upload

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/upload` | Session+CSRF | Upload single file to folder |
| POST | `/upload-album` | Session+CSRF | Upload album (2+ files) |

### Tags

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/tag-categories` | Session | List tag categories |
| GET | `/api/tag-presets` | Session | Preset tags by category |
| POST | `/api/tag-presets` | Session+CSRF | Add preset tag |
| POST | `/api/photos/{id}/tag` | Session+CSRF | Add tag to photo |
| DELETE | `/api/photos/{id}/tag/{tag_id}` | Session+CSRF | Remove tag |
| POST | `/api/photos/{id}/ai-tags` | Session+CSRF | Generate AI tags (simulation) |

### Search and Batch Operations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/tags/all` | Session | All tags (for autocomplete) |
| GET | `/api/photos/search?tags=` | Session | Search by tags (space-separated) |
| POST | `/api/photos/batch-delete` | Session+CSRF | Batch delete |
| POST | `/api/photos/batch-ai-tags` | Session+CSRF | Batch AI tag generation |

### External AI Service API

These endpoints are for external AI services and require API key authentication:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/ai/photos/untagged` | API Key | Photos without tags (limit 10) |
| POST | `/api/ai/photos/{id}/tags` | API Key | Set tags (array of strings) |
| GET | `/api/ai/stats` | API Key | Tagging statistics |

**Usage:**
```bash
# Set API key
export SYNTH_AI_API_KEY=your-secret-key

# Get untagged photos
curl -H "X-API-Key: $SYNTH_AI_API_KEY" http://localhost:8000/api/ai/photos/untagged

# Set tags for a photo
curl -X POST \
  -H "X-API-Key: $SYNTH_AI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '["portrait", "outdoor", "happy"]' \
  http://localhost:8000/api/ai/photos/{photo_id}/tags
```

## Security

- **Authentication**: Session-based with HTTP-only cookies (7 days expiry)
- **Password Hashing**: bcrypt via passlib (auto-migrates legacy SHA-256 hashes)
- **CSRF Protection**: Token validation for all mutating requests
- **Path Traversal Prevention**: File path validation for uploads/thumbnails
- **API Key**: Separate authentication for external AI service endpoints

## Database Schema

**users** — user accounts
- id, username, password_hash, password_salt, display_name, default_folder_id, created_at

**sessions** — login sessions
- id, user_id, created_at, expires_at

**folders** — folder hierarchy
- id, name, user_id, parent_id, created_at

**folder_permissions** — sharing permissions
- id, folder_id, user_id, permission (viewer/editor), granted_by, granted_at

**photos** — uploaded files
- id, filename, original_name, uploaded_at, ai_processed, album_id, position, media_type, folder_id, user_id

**albums** — photo albums
- id, name, created_at, folder_id, user_id

**tags** — photo tags
- id, photo_id, tag, category_id, confidence

**tag_categories** — tag categories
- id, name, color

**tag_presets** — preset tag library
- id, name, category_id

## Tech Stack

- Python 3.11
- FastAPI
- SQLite
- Jinja2
- Pillow (image processing)
- OpenCV (video thumbnails)
- Passlib + bcrypt (password hashing)
