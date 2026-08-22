# Test Coverage Report

**Total Tests:** 358 pytest + 27 Jest
**Generated:** 2026-08-22

## Summary by Category

| Category | Count | Status |
|----------|-------|--------|
| Unit Tests (pytest) | 161 | ✅ Comprehensive |
| Integration Tests (pytest) | 180 | ✅ Good Coverage |
| Service Tests (pytest, `tests/test_*.py`) | 17 | ✅ Good Coverage |
| JS Unit Tests (Jest, `tests/js/`) | 27 | ✅ Growing |
| E2E Tests (Playwright) | 8 | ⚠️ Basic (manual checklist covers the rest) |
| Manual Checklist | 25 | 📋 Available |

---

## Detailed Coverage

### 1. Unit Tests (`tests/unit/`) — 161 tests

| Area | Covers |
|------|--------|
| Encryption | KEK/DEK derivation, SGE1 chunked envelope roundtrip, range decryption, recovery keys, DEK cache (expiry, thread-safety) |
| JXL encoder | cjxl command construction, progressive flags, lossless JPEG transcode |
| Storage | Local/S3 backends, random-access readers |
| Repositories / services | Isolated component behaviour |

### 2. Integration Tests (`tests/integration/`) — 180 tests

| File | Covers |
|------|--------|
| `test_auth.py` | Login flow, sessions, CSRF, encryption key generation |
| `test_folders.py` | Folder CRUD, hierarchy, sharing permissions (viewer/editor) |
| `test_gallery.py` | Gallery view, file access control, thumbnails, sorting |
| `test_upload.py` | Uploads (encrypted on disk), album upload, bulk upload, retrieval |
| `test_upload_jxl.py` | JXL transcode on upload, fallbacks, content negotiation |
| `test_video_mkv.py` | MKV upload, probing, range serving |
| `test_note_upload.py` | Text-note items: formats, validation, encrypted storage, albums, batch download |
| `test_albums.py` / `test_album_name_not_null.py` | Album CRUD, name validation, NOT NULL migration |
| `test_batch_download.py` | ZIP streaming, format conversion, single-file downloads |
| `test_ai_tagging.py` / `test_ai_tagging_sse.py` | AI job queue, API-key auth, SSE progress |
| `test_migration_legacy_users.py` | Alembic upgrade path for legacy databases with relic columns |
| `test_security_upload_spoofing.py` | MIME spoofing rejection |
| `test_sort_preference.py`, `test_lightbox_url.py`, `backup/` | Sort persistence, lightbox URLs, backup/restore |

### 3. JS Unit Tests (`tests/js/`) — 27 tests

| File | Covers |
|------|--------|
| `gallery-masonry.test.js` | Date parsing, sort comparison, column count, height estimation |
| `navigation.test.js` | Folder URL building (subpath), note extension, sort labels |
| `gallery-search.test.js` | Query parsing (negative tags), suggestion application, API URL building |

### 4. E2E Tests (`tests/e2e/`) — 8 tests

Upload modal, sort order, lightbox open/navigate/close, masonry layout
stability, album lightbox context, direct photo URLs. Skipped automatically
when `pytest-playwright` is not installed; see
[`tests/manual/TEST_CHECKLIST.md`](manual/TEST_CHECKLIST.md) for the manual
scenarios that complement them.

---

## Regenerating

```bash
pytest --collect-only -q | tail -1   # pytest count
npx jest --listTests | wc -l         # Jest files (npm test to run)
```
