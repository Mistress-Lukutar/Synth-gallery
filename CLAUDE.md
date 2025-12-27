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

Access at http://localhost:8000

## Architecture

```
app/
├── main.py          # FastAPI routes and application entry point
├── database.py      # SQLite connection, schema, and queries
├── templates/       # Jinja2 templates (base.html, gallery.html, photo.html)
└── static/          # CSS styling (dark theme)
```

**Data directories** (created at runtime):
- `uploads/` - Original uploaded photos
- `thumbnails/` - Auto-generated 400x400 thumbnails
- `gallery.db` - SQLite database

### Key Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Main gallery grid view |
| `/photo/{id}` | GET | Photo detail with tags |
| `/upload` | POST | Upload photo (creates thumbnail) |
| `/api/photos/untagged` | GET | Returns 10 untagged photos for AI service |
| `/api/photos/{id}/tags` | POST | AI service endpoint to set tags |

### Database Schema

- **photos**: id, filename, original_name, uploaded_at, ai_processed
- **tags**: id, photo_id, tag, confidence (cascading delete on photo_id)

## Image Processing

Photos are uploaded with UUID-based filenames. Thumbnails (400x400, JPEG quality 85) are generated automatically using Pillow. If thumbnail creation fails, the upload is rolled back.

## AI Integration

The `/api/photos/untagged` and `/api/photos/{id}/tags` endpoints are designed for an external AI tagging service. The `ai_processed` flag tracks which photos have been processed.
