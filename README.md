# Synth

Local gallery for storing and organizing AI-generated images.

## Features

- Upload images (jpg, png, gif, webp) and videos (mp4, webm)
- Create albums from multiple files
- Tag system with categories (Subject, Location, Mood, Style, Event, Other)
- Tag search with autocomplete
- AI tag generation (simulation)
- Batch operations (delete, tag)
- Dark theme UI

## Quick Start

### Docker (recommended)

```bash
docker-compose up --build
```

### Local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

## Project Structure

```
app/
├── main.py          # FastAPI routes and application logic
├── database.py      # SQLite schema and connection
├── templates/       # Jinja2 templates (base, gallery, photo)
└── static/          # CSS and icons

uploads/             # Original files (created automatically)
thumbnails/          # 400x400 previews (created automatically)
gallery.db           # SQLite database
```

## API

### Upload

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload` | Upload single file |
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

**photos** — uploaded files
- id, filename, original_name, uploaded_at, ai_processed, album_id, position, media_type

**albums** — photo albums
- id, name, created_at

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
