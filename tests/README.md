# Testing Documentation

This document describes the testing strategy and all available tests for the Synth Gallery application.

## Testing Levels

```
┌─────────────────────────────────────────────────────────────┐
│                    E2E Tests (Playwright)                   │
│         Browser automation - tests user workflows           │
├─────────────────────────────────────────────────────────────┤
│                 Integration Tests (pytest)                  │
│     API endpoints, database operations, authentication      │
├─────────────────────────────────────────────────────────────┤
│                    Unit Tests (pytest)                      │
│   Encryption, services, repositories - isolated testing     │
└─────────────────────────────────────────────────────────────┘
```

## Running Tests

### All Tests
```bash
python -m pytest tests/ -v
```

### By Category
```bash
# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only
python -m pytest tests/integration/ -v

# E2E tests (requires running server)
python -m pytest tests/e2e/ -v --headed

# Specific test file
python -m pytest tests/integration/test_auth.py -v

# Specific test
python -m pytest tests/integration/test_auth.py::TestLoginFlow::test_login_with_valid_credentials -v
```

### With Coverage
```bash
python -m pytest tests/ --cov=app --cov-report=html --cov-report=term
```

## Test Categories

### 1. E2E Tests (`tests/e2e/`)

End-to-end tests using Playwright that simulate real user interactions in a browser.

#### Prerequisites
```bash
pip install pytest-playwright
playwright install
```

#### Available E2E Tests

| Test | Description |
|------|-------------|
| `test_upload_button_opens_modal` | Verifies upload button opens upload modal |
| `test_sort_button_changes_order` | Verifies sort dropdown changes item order |
| `test_lightbox_opens_on_photo_click` | Verifies clicking photo opens lightbox |
| `test_lightbox_navigation` | Verifies prev/next navigation in lightbox |
| `test_lightbox_close_removes_photo_id` | Verifies closing lightbox removes photo_id from URL |
| `test_masonry_layout_no_jumps` | Verifies masonry layout doesn't jump on image load |
| `test_album_opens_lightbox_with_album_context` | Verifies album click opens album photos in lightbox |
| `test_url_with_photo_id_opens_lightbox_on_load` | Verifies direct URL with photo_id opens lightbox |

#### Running E2E Tests
```bash
# With visible browser (for debugging)
python -m pytest tests/e2e/ -v --headed

# Headless mode (CI/CD)
python -m pytest tests/e2e/ -v

# With video recording
python -m pytest tests/e2e/ -v --video=on

# With screenshots on failure
python -m pytest tests/e2e/ -v --screenshot=on

# Specific browser
python -m pytest tests/e2e/ -v --browser=chromium
```

---

### 2. Integration Tests (`tests/integration/`)

Tests for API endpoints, database operations, and authentication flows.

#### Authentication Tests (`test_auth.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestLoginFlow` | `test_login_page_accessible_without_auth` | Login page is public |
| | `test_login_with_valid_credentials` | Valid login creates session |
| | `test_login_with_invalid_password` | Invalid password shows error |
| | `test_login_with_nonexistent_user` | Nonexistent user shows error |
| `TestSessionManagement` | `test_protected_route_redirects_when_not_authenticated` | Auth required for protected pages |
| | `test_protected_route_accessible_when_authenticated` | Authenticated users access protected pages |
| | `test_logout_clears_session` | Logout clears session |
| | `test_session_persists_across_requests` | Session works across multiple requests |
| `TestCSRFProtection` | `test_csrf_cookie_set_on_login_page` | CSRF cookie is set |
| | `test_post_without_csrf_fails` | POST without CSRF token fails |
| `TestEncryptionIntegration` | `test_encryption_keys_generated_on_first_login` | Keys generated on first login |
| | `test_dek_cached_after_login` | DEK cached in memory after login |

#### Folder Tests (`test_folders.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestFolderCreation` | `test_create_folder_via_api` | Folder creation via API works |
| | `test_folder_tree_returns_user_folders` | Folder tree returns user's folders |
| `TestFolderPermissions` | `test_user_can_access_own_folder` | Owner can access own folder |
| | `test_user_cannot_access_others_folder_without_permission` | No access without permission |
| | `test_shared_folder_accessible_to_viewer` | Viewer can access shared folder |
| | `test_viewer_cannot_upload_to_shared_folder` | Viewer cannot upload |
| | `test_editor_can_upload_to_shared_folder` | Editor can upload |
| `TestFolderHierarchy` | `test_nested_folder_creation` | Nested folders work |
| | `test_folder_tree_shows_hierarchy` | Folder tree shows hierarchy |
| `TestFolderDeletion` | `test_delete_folder_removes_contents` | Deletion removes contents |
| | `test_only_owner_can_delete_folder` | Only owner can delete |
| `TestFolderContentAPI` | `test_folder_content_returns_photos_albums` | API returns photos and albums |
| | `test_breadcrumbs_returned_for_folder` | Breadcrumbs returned |

#### Gallery Tests (`test_gallery.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestGalleryView` | `test_gallery_shows_user_content` | Gallery shows user's content |
| | `test_gallery_defaults_to_user_default_folder` | Defaults to default folder |
| `TestFileAccessControl` | `test_owner_can_access_own_file` | Owner can access files |
| | `test_viewer_can_access_shared_file` | Viewer can access shared files |
| | `test_unrelated_user_cannot_access_file` | Unrelated users blocked |
| | `test_file_access_requires_authentication` | Auth required for files |
| `TestThumbnailAccess` | `test_thumbnail_generated_on_upload` | Thumbnails generated on upload |
| | `test_thumbnail_requires_same_permissions_as_original` | Thumbnails have same permissions |
| | `test_thumbnail_regenerated_if_missing` | Thumbnails regenerated if missing |
| `TestGallerySorting` | `test_sort_by_upload_date` | Sort by upload date works |
| | `test_sort_by_taken_date` | Sort by taken date works |
| | `test_folder_content_api_returns_sorted_items` | API returns sorted items |
| `TestAPIResponses` | `test_folder_tree_api_structure` | Folder tree API structure correct |
| | `test_default_folder_api` | Default folder API works |

#### Upload Tests (`test_upload.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestSingleFileUpload` | `test_upload_image_without_encryption` | Upload without encryption |
| | `test_upload_with_encryption_enabled` | Upload with encryption |
| | `test_upload_rejects_invalid_file_type` | Invalid file types rejected |
| | `test_upload_requires_folder_id` | Folder ID required |
| | `test_upload_requires_edit_permission` | Edit permission required |
| `TestAlbumUpload` | `test_upload_album_with_multiple_images` | Album upload works |
| | `test_album_requires_minimum_two_files` | Album requires 2+ files |
| `TestFileRetrieval` | `test_retrieve_uploaded_image` | Can retrieve uploaded image |
| | `test_retrieve_thumbnail_generated` | Thumbnail is generated |
| | `test_unauthenticated_cannot_retrieve_file` | Auth required |
| | `test_download_preserves_file_content` | Download preserves content |
| `TestBulkUpload` | `test_bulk_upload_creates_albums_from_subfolders` | Bulk upload creates albums |

---

### 3. Unit Tests (`tests/unit/`)

#### Encryption Tests (`test_encryption.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestKeyDerivation` | `test_derive_kek_consistent` | KEK derivation is consistent |
| | `test_derive_kek_different_salts` | Different salts produce different KEKs |
| | `test_derive_kek_different_passwords` | Different passwords produce different KEKs |
| `TestDEKGeneration` | `test_generate_dek_unique` | DEK generation produces unique keys |
| | `test_generate_dek_random` | DEK generation is random |
| `TestDEKEncryption` | `test_encrypt_decrypt_dek` | DEK encryption/decryption works |
| | `test_encrypt_dek_different_keks` | Different KEKs produce different ciphertexts |
| | `test_decrypt_with_wrong_kek_fails` | Wrong KEK fails decryption |
| `TestFileEncryption` | `test_encrypt_decrypt_file` | File encryption/decryption works |
| | `test_encrypt_file_different_deks` | Different DEKs produce different ciphertexts |
| | `test_decrypt_with_wrong_dek_fails` | Wrong DEK fails decryption |
| | `test_encrypt_empty_file` | Empty file encryption works |
| | `test_encrypt_large_file` | Large file encryption works |
| | `test_ciphertext_not_equal_plaintext` | Ciphertext differs from plaintext |
| `TestRecoveryKeys` | `test_generate_recovery_key_format` | Recovery key format is correct |
| | `test_recovery_key_unique` | Recovery keys are unique |
| | `test_parse_recovery_key_usable_for_encryption` | Recovery key usable for encryption |
| | `test_recovery_key_encryption` | Recovery key encryption works |
| | `test_recovery_key_case_insensitive` | Recovery keys case-insensitive |
| | `test_recovery_key_without_dashes` | Recovery keys work without dashes |
| | `test_recovery_key_with_whitespace` | Recovery keys work with whitespace |
| | `test_recovery_key_only_valid_chars` | Recovery keys only use valid characters |
| | `test_recovery_key_roundtrip_100_times` | Recovery key roundtrip test |
| `TestDEKCache` | `test_cache_stores_and_retrieves` | DEK cache stores and retrieves |
| | `test_cache_returns_none_for_missing` | Cache returns None for missing entries |
| | `test_cache_expires` | Cache entries expire |
| | `test_cache_invalidation` | Cache invalidation works |
| | `test_cache_clear_expired` | Clearing expired entries works |
| | `test_cache_thread_safety` | Cache is thread-safe |
| `TestSaltGeneration` | `test_generate_salt_unique` | Salts are unique |
| | `test_generate_salt_random` | Salts are random |

---

### 4. Service Tests (`tests/test_services.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestFolderService` | `test_create_regular_folder` | Folder creation |
| | `test_create_folder_with_parent` | Nested folder creation |
| | `test_create_folder_in_another_users_folder_fails` | Permission check |
| | `test_update_folder` | Folder update |
| | `test_update_folder_not_owner_fails` | Owner check |
| | `test_delete_folder` | Folder deletion |
| | `test_is_descendant` | Descendant check |
| | `test_is_not_descendant` | Non-descendant check |
| `TestPermissionService` | `test_grant_permission` | Permission granting |
| | `test_grant_invalid_permission_fails` | Invalid permission check |
| | `test_get_user_permission_owner` | Owner permission |
| | `test_get_user_permission_viewer` | Viewer permission |
| | `test_has_permission_hierarchy` | Permission hierarchy |
| | `test_can_access` | Access check |
| | `test_can_edit` | Edit check |
| `TestSafeService` | `test_is_safe_folder` | Safe folder detection |
| | `test_get_safe_by_folder` | Safe retrieval by folder |
| | `test_configure_safe` | Safe configuration |
| | `test_configure_nonexistent_safe_fails` | Nonexistent safe check |
| `TestPhotoService` | `test_move_photo_not_found` | Move nonexistent photo |
| | `test_move_photo_in_album_fails` | Move photo in album |
| | `test_batch_move_no_permission_on_dest` | Batch move permission |
| | `test_add_photos_to_album_no_permission` | Add photos permission |
| | `test_move_album_not_found` | Move nonexistent album |
| `TestUploadService` | `test_delete_photo_success` | Photo deletion |
| | `test_delete_photo_not_found` | Delete nonexistent photo |
| | `test_delete_album_success` | Album deletion |
| | `test_validate_file_rejects_empty` | Empty file rejection |
| | `test_get_media_type_from_content_type` | Media type detection |
| | `test_get_media_type_from_extension_for_safe` | Extension detection |

---

### 5. Safe Files Tests (`tests/test_safe_files.py`)

| Class | Test | Description |
|-------|------|-------------|
| `TestSafeFileAccess` | `test_safe_thumbnail_returns_404_for_nonexistent_photo` | Safe thumbnail 404 |
| | `test_safe_file_endpoints_use_permission_service` | Permission service usage |
| `TestSafeFileThumbnail` | `test_thumbnail_endpoint_returns_202_for_missing_thumbnail` | 202 for missing thumbnail |
| | `test_permission_service_has_can_access_photo` | Service has photo access method |

---

## Manual Testing Checklist

For manual testing scenarios, see: [`tests/manual/TEST_CHECKLIST.md`](manual/TEST_CHECKLIST.md)

Key areas for manual testing:
- UI responsiveness
- Browser compatibility
- Mobile device testing
- Keyboard navigation
- Accessibility (screen readers)
- Performance with large galleries (1000+ items)

## Debugging Failed Tests

### E2E Tests
```bash
# Run with visible browser
pytest tests/e2e/ -v --headed --slowmo=500

# Run single test with debugging
pytest tests/e2e/test_gallery.py::test_lightbox_opens_on_photo_click -v --headed --pdb

# Generate trace
pytest tests/e2e/ -v --tracing=retain-on-failure
```

### Integration/Unit Tests
```bash
# Run with debugger
pytest tests/integration/test_auth.py -v --pdb

# Run with detailed output
pytest tests/integration/test_auth.py -v --tb=long

# Run with warnings
pytest tests/ -v -W error
```

## Test Data

Tests use:
- In-memory SQLite database (reset for each test)
- Test fixtures in `tests/conftest.py`
- Sample images in `tests/fixtures/` (if needed)

## CI/CD Integration

Example GitHub Actions workflow:

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-dev.txt
      - name: Run unit and integration tests
        run: pytest tests/unit tests/integration -v
      - name: Run E2E tests
        run: |
          playwright install
          pytest tests/e2e -v
```

## Writing New Tests

### E2E Test Template
```python
def test_feature_description(logged_in_page):
    """Test description: what is being tested and expected result."""
    page = logged_in_page
    
    # Arrange: Set up test state
    page.goto("http://localhost:8000/?folder_id=xxx")
    
    # Act: Perform actions
    page.click('#button-id')
    
    # Assert: Verify expected outcome
    expect(page.locator('#expected-element')).to_be_visible()
```

### Integration Test Template
```python
def test_api_endpoint(client, auth_user):
    """Test API endpoint description."""
    # Act
    response = client.get('/api/endpoint')
    
    # Assert
    assert response.status_code == 200
    assert 'expected_key' in response.json()
```

### Unit Test Template
```python
def test_function_description():
    """Test function description."""
    # Arrange
    input_data = ...
    expected_output = ...
    
    # Act
    result = function_to_test(input_data)
    
    # Assert
    assert result == expected_output
```
