# Synth

Local gallery for storing and organizing AI-generated images with multi-user support and folder sharing.

## Features

- **User Authentication** — Registration, login, personal galleries
- **Folder System** — Hierarchical folder structure for organizing content
- **Sharing & Permissions** — Share folders with other users as Viewer (read-only) or Editor (can upload/delete)
- **Media Support** — Images (jpg, png, gif, webp) and videos (mp4, webm)
- **Albums** — Group multiple files into albums
- **Tag System** — Categories (Subject, Location, Mood, Style, Event, Other) with presets
- **Tag Search** — Autocomplete search across your accessible content
- **AI Tags** — Auto-generate tags for photos (simulation mode)
- **Batch Operations** — Delete or tag multiple items at once
- **Dark Theme** — Modern dark UI

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

### User Management

Create users via CLI:

```bash
python manage_users.py add <username> <password> [--display-name "Display Name"]
python manage_users.py list
python manage_users.py delete <username>
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
├── main.py          # FastAPI routes and application logic
├── database.py      # SQLite schema, queries, permissions
├── templates/       # Jinja2 templates (base, gallery, photo, login)
└── static/          # CSS and icons

uploads/             # Original files (created automatically)
thumbnails/          # 400x400 previews (created automatically)
gallery.db           # SQLite database
manage_users.py      # CLI for user management
```

## API

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate user |
| GET | `/logout` | Logout |

### Folders

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/folders` | List user's folders and shared folders |
| POST | `/api/folders` | Create folder |
| PUT | `/api/folders/{id}` | Update folder |
| DELETE | `/api/folders/{id}` | Delete folder (owner only) |

### Folder Permissions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/folders/{id}/permissions` | List folder permissions |
| POST | `/api/folders/{id}/permissions` | Add user permission |
| PUT | `/api/folders/{id}/permissions/{user_id}` | Update permission |
| DELETE | `/api/folders/{id}/permissions/{user_id}` | Remove permission |
| GET | `/api/users/search?q=` | Search users for sharing |

### Upload

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload` | Upload single file to folder |
| POST | `/upload-album` | Upload album (2+ files) |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tag-categories` | List tag categories |
| GET | `/api/tag-presets` | Preset tags by category |
| POST | `/api/tag-presets` | Add preset tag |
| POST | `/api/photos/{id}/tag` | Add tag to photo |
| DELETE | `/api/photos/{id}/tag/{tag_id}` | Remove tag |
| POST | `/api/photos/{id}/ai-tags` | Generate AI tags |

### Search and Batch Operations

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tags/all` | All tags (for autocomplete) |
| GET | `/api/photos/search?tags=` | Search by tags (space-separated) |
| POST | `/api/photos/batch-delete` | Batch delete |
| POST | `/api/photos/batch-ai-tags` | Batch AI tag generation |

### External AI Service

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/photos/untagged` | Photos without tags (limit 10) |
| POST | `/api/photos/{id}/tags` | Set tags (array of strings) |

## Database Schema

**users** — user accounts
- id, username, password_hash, display_name, created_at

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
