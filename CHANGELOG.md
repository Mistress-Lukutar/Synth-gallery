# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.5] - 2026-02-16

### Added
- **Safes (Encrypted Vaults)** - Folders with independent end-to-end encryption
  - Each safe has its own encryption key (DEK), separate from user's master key
  - Password protection (PBKDF2) or hardware key (WebAuthn) unlock
  - Visual indicator in sidebar
  - Supports folders inside safe
  - **True E2E encryption**: Server never sees decrypted content
    - Files stored encrypted on disk
    - Server returns encrypted files with X-Encryption: e2e header
    - Client decrypts files in browser using SafeCrypto
  - Client-side thumbnail generation for safe uploads (E2E encryption)
  - No sharing support (owner-only access)
  - Requires HTTPS or localhost (Web Crypto API)

### Fixed
- Fixed safe folder tree display and navigation
- Fixed Edit Safe modal incorrectly opening for folders inside safes
- Fixed SPA navigation for upload/delete/album-editor
- Fixed upload modal: close after success, accumulate files across selections
- Fixed sort persistence and upload error handling

## [0.8.4] - 2026-01-10

### Fixed
- **Critical**: Encryption key loss when changing default folder
  - `INSERT OR REPLACE` was wiping `encrypted_dek` and `dek_salt` columns
  - Now uses proper `UPDATE` for existing rows
- Thumbnail regeneration failing due to tuple unpacking mismatch
  - `create_thumbnail_bytes()` returns `(bytes, width, height)` but code expected only `bytes`
  - Fixes auto-regeneration and admin maintenance panel
- Progressive image loading in lightbox
  - Shows cached thumbnail immediately, loads full image in background
  - Images fill available space regardless of resolution
- Removed CPU-intensive pulse animation from gallery placeholders
- Album indicator no longer flickers when navigating within album
- Mobile swipe animation no longer briefly shows previous image

## [0.8.3] - 2026-01-10

### Added
- Instant gallery loading with aspect-ratio placeholders
  - Thumbnail dimensions stored in database for immediate layout
  - CSS shimmer animation while images load
  - Legacy photos migrate dimensions on first view
- Sidebar folder tree caching for instant display on navigation
  - Cached in sessionStorage, updates in background if changed

### Fixed
- Thumbnail orientation for images with EXIF rotation data
- Gallery layout width calculation (proper calc instead of flex)
- ResizeObserver for responsive masonry layout on zoom/resize
- Mobile sidebar overlay now properly blocks interactions behind it

### Changed
- Header scrolls with page on mobile (not sticky)
- Improved mobile experience with proper overlay touch handling

## [0.8.2] - 2026-01-08

### Added
- Full backup system for encrypted content (Closes #9)
  - Creates ZIP archive with database and original media files (thumbnails auto-regenerate)
  - SHA-256 checksums for integrity verification
  - Manifest with metadata (version, stats, user list)
  - Admin UI at `/admin/backups` for creating, verifying, downloading, and restoring backups
  - CLI commands: `backup`, `backup-list`, `restore`, `verify`
- Recovery Key system for password loss recovery
  - Generate one-time recovery key via CLI: `python manage_users.py recovery-key <username> <password>`
  - Recover access with: `python manage_users.py recover <username> <recovery_key>`
  - DEK encrypted with both password-derived KEK and recovery key
- Automatic backup scheduler
  - Configurable via `BACKUP_SCHEDULE` env var (daily/weekly/disabled)
  - Background thread checks hourly and creates backups as needed
  - Automatic rotation keeps last N backups (`BACKUP_ROTATION_COUNT`)
- Configurable backup storage path via `BACKUP_PATH` environment variable
- Hardware key authentication (WebAuthn/FIDO2) for passwordless login (#10)
  - Support for YubiKey and other FIDO2 security keys
  - Alternative to password-based authentication
  - Hardware key management in user settings (`/settings`)
  - Encrypted DEK storage per credential for seamless file decryption
  - Login page automatically detects if user has registered keys
  - Works from any origin/port (RP ID auto-detected from request)
  - New dependency: `webauthn>=2.0.0`

### Fixed
- Password change now properly re-encrypts DEK with new password
  - Previously changing password broke file decryption
  - CLI syntax changed: `passwd <username> <old_password> <new_password>`

### Changed
- Admin backup page now shows both database-only and full backups
- Backup service refactored into FullBackupService class
- User settings page added (`/settings`) accessible from header
- Settings gear icon added to user menu in navigation

## [0.8.1] - 2026-01-06

### Fixed
- Masonry layout visual bugs: scroll position now preserved during resize/zoom
- Session without DEK causing missing images after server restart
  - Users with encrypted files now prompted to re-enter password when DEK cache expires
- Folder photo count now includes photos in all subfolders recursively

### Changed
- Subfolders displayed as horizontal tiles above gallery instead of in masonry grid
  - Cleaner visual separation between navigation and content
  - Responsive design for mobile devices

## [0.8.0] - 2026-01-05

### Added
- Per-user media encryption (Closes #4)
  - AES-256-GCM encryption for uploaded files and thumbnails
  - Encryption key derived from user password (PBKDF2-SHA256, 600k iterations)
  - Automatic encryption on upload for logged-in users
  - Transparent decryption when serving files
  - Shared folder support: files decrypted via owner's key when owner is online
  - CLI command for migrating existing files: `python manage_users.py encrypt-files <username> <password>`
  - New dependency: `cryptography>=42.0.0`

### Security
- Files at rest are now encrypted per-user
- Admin/filesystem access cannot reveal uploaded content
- Password loss = data loss (no recovery mechanism)

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

[0.8.5]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.8.4...v0.8.5
[0.8.4]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Mistress-Lukutar/Synth-gallery/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Mistress-Lukutar/Synth-gallery/releases/tag/v0.1.0
