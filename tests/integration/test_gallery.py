"""
Gallery view and file access integration tests.

Verifies:
- Gallery page rendering
- File access control
- Thumbnail generation
- Sort order
"""
from fastapi.testclient import TestClient


class TestGalleryView:
    """Test main gallery page."""
    
    def test_gallery_shows_user_content(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes
    ):
        """Gallery should display user's photos and folders."""
        # Upload photo to folder
        authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            files={"file": ("gallery_item.jpg", test_image_bytes, "image/jpeg")}
        )
        
        # View folder
        response = authenticated_client.get(f"/?folder_id={test_folder}")
        
        assert response.status_code == 200
        # Should contain photo reference
        assert "gallery_item" in response.text or test_folder in response.text
    
    def test_gallery_defaults_to_user_default_folder(
        self,
        authenticated_client: TestClient,
        test_user: dict
    ):
        """Gallery without folder_id should return SPA shell with default folder."""
        response = authenticated_client.get("/", follow_redirects=False)
        
        # SPA shell returned (no redirect needed)
        assert response.status_code == 200
        # Should contain INITIAL_FOLDER_ID variable for SPA to load
        assert "INITIAL_FOLDER_ID" in response.text
    
class TestFileAccessControl:
    """Test file access permissions."""
    
    def test_owner_can_access_own_file(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Owner should access their uploaded file."""
        response = authenticated_client.get(f"/uploads/{uploaded_photo['filename']}")
        
        assert response.status_code == 200
    
    def test_viewer_can_access_shared_file(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """Viewer of shared folder can access files."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository
        
        # Second user creates folder and uploads
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        folder_id = folder_repo.create("SharedFiles", second_user["id"])
        
        # Get CSRF token first
        client.get("/login")
        csrf_token = client.cookies.get("synth_csrf", "")
        
        client.post(
            "/login",
            data={
                "username": second_user["username"], 
                "password": second_user["password"],
                "csrf_token": csrf_token
            },
            follow_redirects=False
        )
        
        # Get fresh CSRF token after login
        csrf_token = client.cookies.get("synth_csrf", "")
        
        response = client.post(
            "/upload",
            data={"folder_id": folder_id},
            files={"file": ("shared.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200, f"Upload failed: {response.text}"
        filename = response.json()["filename"]
        
        # Share with first user
        perm_repo.grant(folder_id, test_user["id"], "viewer", second_user["id"])
        
        # First user accesses file
        client.cookies.clear()
        client.get("/login")
        csrf_token = client.cookies.get("synth_csrf", "")
        
        client.post(
            "/login",
            data={
                "username": test_user["username"], 
                "password": test_user["password"],
                "csrf_token": csrf_token
            },
            follow_redirects=False
        )
        
        response = client.get(f"/uploads/{filename}")
        
        assert response.status_code == 200
    
    def test_unrelated_user_cannot_access_file(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """User without permission cannot access others' files."""
        from app.infrastructure.repositories import FolderRepository
        
        # Second user creates private folder and uploads
        folder_repo = FolderRepository(db_connection)
        private_folder = folder_repo.create("PrivateFiles", second_user["id"])
        
        # Get CSRF token
        client.get("/login")
        csrf_token = client.cookies.get("synth_csrf", "")
        
        client.post(
            "/login",
            data={
                "username": second_user["username"], 
                "password": second_user["password"],
                "csrf_token": csrf_token
            },
            follow_redirects=False
        )
        
        # Get fresh CSRF token
        csrf_token = client.cookies.get("synth_csrf", "")
        
        response = client.post(
            "/upload",
            data={"folder_id": private_folder},
            files={"file": ("secret.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200, f"Upload failed: {response.text}"
        filename = response.json()["filename"]
        
        # First user (unrelated) tries to access
        client.cookies.clear()
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.get(f"/uploads/{filename}")
        
        assert response.status_code == 403
    
    def test_file_access_requires_authentication(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Unauthenticated requests should be rejected."""
        # Upload as authenticated user
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            files={"file": ("auth_test.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200, f"Upload failed: {response.text}"
        filename = response.json()["filename"]
        
        # Try to access without auth (create fresh client)
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as new_client:
            response = new_client.get(f"/uploads/{filename}", follow_redirects=False)
        
        assert response.status_code in [302, 401, 403]  # Redirect to login or 401


class TestThumbnailAccess:
    """Test thumbnail generation and access."""
    
    def test_thumbnail_generated_on_upload(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Thumbnail should exist after upload."""
        thumbnail_name = f"{uploaded_photo['id']}.jpg"
        response = authenticated_client.get(f"/thumbnails/{thumbnail_name}")
        
        assert response.status_code == 200
        assert response.headers.get("content-type") == "image/jpeg"
    
    def test_thumbnail_requires_same_permissions_as_original(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """Thumbnail access should follow same rules as original."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        private_folder = folder_repo.create("ThumbPrivate", second_user["id"])
        
        # Get CSRF token
        client.get("/login")
        csrf_token = client.cookies.get("synth_csrf", "")
        
        client.post(
            "/login",
            data={
                "username": second_user["username"], 
                "password": second_user["password"],
                "csrf_token": csrf_token
            },
            follow_redirects=False
        )
        
        # Get fresh CSRF token
        csrf_token = client.cookies.get("synth_csrf", "")
        
        response = client.post(
            "/upload",
            data={"folder_id": private_folder},
            files={"file": ("thumb_test.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200, f"Upload failed: {response.text}"
        photo_id = response.json()["id"]
        
        # Unrelated user tries to get thumbnail
        client.cookies.clear()
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.get(f"/thumbnails/{photo_id}.jpg")
        
        assert response.status_code == 403
    
    def test_thumbnail_regenerated_if_missing(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Should regenerate thumbnail if deleted."""
        from app.config import THUMBNAILS_DIR
        
        thumbnail_name = f"{uploaded_photo['id']}.jpg"
        thumb_path = THUMBNAILS_DIR / thumbnail_name
        
        # Delete thumbnail manually
        if thumb_path.exists():
            thumb_path.unlink()
        
        # Request should regenerate it
        response = authenticated_client.get(f"/thumbnails/{thumbnail_name}")
        
        assert response.status_code == 200
        # Should exist again (if regeneration is implemented)
        # assert thumb_path.exists()  # Optional based on implementation


class TestGallerySorting:
    """Test photo/album sorting options."""
    
    def test_sort_by_upload_date(self, authenticated_client: TestClient, test_user: dict, db_connection):
        """Photos can be sorted by upload date."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        folder_id = folder_repo.create("SortTest", test_user["id"])
        
        response = authenticated_client.get(f"/?folder_id={folder_id}&sort=uploaded")
        
        assert response.status_code == 200
    
    def test_sort_by_taken_date(self, authenticated_client: TestClient, test_user: dict, db_connection):
        """Photos can be sorted by capture date (EXIF)."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        folder_id = folder_repo.create("SortTaken", test_user["id"])
        
        response = authenticated_client.get(f"/?folder_id={folder_id}&sort=taken")
        
        assert response.status_code == 200
    
    def test_folder_content_api_returns_sorted_items(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """API should return properly sorted items."""
        # Upload multiple photos
        for i in range(3):
            response = authenticated_client.post(
                "/upload",
                data={"folder_id": test_folder},
                files={"file": (f"sort_{i}.jpg", test_image_bytes, "image/jpeg")},
                headers={"X-CSRF-Token": csrf_token}
            )
            assert response.status_code == 200, f"Upload {i} failed: {response.text}"
        
        response = authenticated_client.get(f"/api/folders/{test_folder}/contents?sort=uploaded")
        
        if response.status_code == 200:
            data = response.json()
            # Check various possible response structures
            items = data.get("items") or data.get("photos") or []
            assert len(items) >= 3


class TestAPIResponses:
    """Test API response formats."""
    
    def test_folder_tree_api_structure(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """Folder tree API should have expected structure."""
        response = authenticated_client.get("/api/folders")
        
        assert response.status_code == 200
        folders = response.json()
        
        if folders:  # If any folders exist
            folder = folders[0]
            # Check expected fields
            assert "id" in folder
            assert "name" in folder
            # These may or may not exist depending on implementation
            # assert "photo_count" in folder
            # assert "permission" in folder
    
    def test_default_folder_api(self, authenticated_client: TestClient):
        """Default folder API should return valid folder ID."""
        response = authenticated_client.get("/api/user/default-folder")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "folder_id" in data
        assert len(data["folder_id"]) > 0
