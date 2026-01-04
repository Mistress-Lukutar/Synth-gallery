# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-01-04

### Added
- Drag-and-drop to move photos and albums between folders
  - Visual drop zones on folder items in sidebar
  - Works with both individual photos and albums
- Default folder selection in user settings
  - Set preferred folder for new uploads
  - Persisted per-user in database
- Collapsible folders in sidebar tree
  - Expand/collapse toggle for folders with children
  - Collapse state persisted per-user
  - New API endpoints: `/user/collapsed`, `/{folder_id}/toggle-collapse`

## [0.6.2] - 2026-01-03

### Fixed
- Fixed bcrypt 5.0 compatibility issue causing user creation to fail via CLI script
  - Replaced passlib.hash.bcrypt with direct bcrypt library usage

## [0.6.1] - 2026-01-03

### Added
- Thumbnail management system:
  - Auto-regeneration of missing thumbnails on access (no more 404 errors)
  - Admin maintenance page (`/admin/maintenance`) with thumbnail statistics
  - Bulk regeneration of all missing thumbnails
  - Cleanup of orphaned thumbnails (thumbnails without photos)
- New service module: `services/thumbnail.py`
- Mobile-friendly lightbox navigation:
  - Swipe left/right to navigate between photos with slide animations
  - Swipe up/down to close lightbox
  - Navigation arrows hidden on mobile (replaced by swipes)

### Fixed
- Mobile: Folder picker (sidebar) now closes when tapping outside or swiping left (#7)
- Mobile: Pinch-to-zoom gestures no longer conflict with swipe navigation in lightbox (#7)

## [0.6.0] - 2025-12-31

### Added
- Album management functionality:
  - Add/remove photos from albums
  - Reorder photos with drag-and-drop
  - Set custom cover photo for albums
  - Album editor side panel in lightbox
- Database backup system:
  - Automatic backup before database migrations
  - Manual backup/restore/delete via admin UI (`/admin/backups`)
  - Backup rotation (keeps last 5)
  - Download backups as files
- Admin role for users (`is_admin` flag)
- CLI commands: `admin`/`unadmin` for `manage_users.py`

### Changed
- Albums table includes `cover_photo_id` field
- Users table includes `is_admin` field

## [0.5.0] - 2025-12-30

### Added
- Photo metadata extraction from EXIF and PNG data
- Sort photos by capture date (date taken) or upload date
- Per-user, per-folder sort preference persistence
- New service module: `services/metadata.py`
- Album dates now display the latest photo capture date
- Bulk folder upload: select a folder to upload its structure
  - Root-level files become individual photos
  - Subfolders become albums (named after folder)
  - Uses native browser folder picker (`webkitdirectory`)
- Lightbox now displays capture date and upload date with monochrome icons

### Changed
- Photos table includes `taken_at` field for capture date
- Gallery view supports sorting toggle in UI
- Database schema includes `user_folder_preferences` table
- `taken_at` now defaults to upload date when metadata extraction fails (instead of NULL)
- Metadata extraction now supports all file types:
  - JPEG, PNG, WebP: EXIF + XMP
  - GIF: info dict, comments, XMP
  - MP4, WebM: ffprobe (creation_time)

### Fixed
- Folder deletion now properly removes photos and albums from database

## [0.4.0] - 2025-12-30

### Changed
- **Breaking:** AI service endpoints moved from `/api/photos/untagged` to `/api/ai/photos/untagged`
- Refactored monolithic `main.py` (1213 lines) into modular architecture
- Password hashing switched from SHA-256 to bcrypt (legacy passwords auto-migrate)

### Added
- CSRF protection for all mutating requests
- API key authentication for external AI service (`X-API-Key` header)
- Path traversal prevention for file access
- New modules: `config.py`, `middleware.py`, `dependencies.py`
- Route modules: `routes/auth.py`, `routes/gallery.py`, `routes/folders.py`, `routes/tags.py`, `routes/api.py`
- Service modules: `services/media.py`
- AI service statistics endpoint `/api/ai/stats`

### Security
- bcrypt password hashing with automatic legacy migration
- CSRF tokens validated via `X-CSRF-Token` header
- AI endpoints require `SYNTH_AI_API_KEY` environment variable

## [0.3.0] - 2025-12-29

### Added
- Hierarchical folder system for organizing content
- Folder sharing with permissions (Viewer/Editor roles)
- Visual indicators for shared folders in sidebar
- User search for sharing dialogs
- Folder breadcrumb navigation

### Changed
- Photos and albums now belong to folders
- Default "My Gallery" folder created for new users

## [0.2.0] - 2025-12-29

### Added
- User authentication system with login/logout
- Session-based authentication with HTTP-only cookies
- User management CLI (`manage_users.py`)
- Protected routes requiring authentication

### Security
- Password hashing with salt (SHA-256)
- Session expiration (7 days)
- Automatic cleanup of expired sessions

## [0.1.0] - 2025-12-28

### Added
- Initial release
- Photo and video upload (jpg, png, gif, webp, mp4, webm)
- Automatic thumbnail generation (400x400)
- Album support for grouping multiple files
- Tag system with categories (Subject, Location, Mood, Style, Event, Other)
- Preset tag library
- Tag search with autocomplete
- AI tag generation (simulation mode)
- Batch operations (delete, AI tagging)
- Dark theme UI
- Responsive masonry grid layout
- Lightbox photo viewer

[0.7.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Mistress-Lukutar/Synth-gallery/releases/tag/v0.1.0
