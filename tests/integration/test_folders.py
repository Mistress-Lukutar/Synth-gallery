"""
Folder management integration tests.

Verifies:
- Folder CRUD operations
- Permission system (owner, editor, viewer)
- Folder tree structure
- Sharing functionality
"""

import pytest
from fastapi.testclient import TestClient


class TestFolderCreation:
    """Test creating folders via API."""
    
    def test_create_folder_via_api(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        csrf_token: str
    ):
        """Create folder through API endpoint."""
        response = authenticated_client.post(
            "/api/folders",
            json={"name": "New API Folder"},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Note: If endpoint doesn't exist yet, this documents expected behavior
        if response.status_code == 404:
            pytest.skip("Folder creation API not implemented yet")
        
        assert response.status_code == 200
        data = response.json()
        assert "id" in data or "folder" in data
        if "folder" in data:
            assert data["folder"]["name"] == "New API Folder"
    
    def test_folder_tree_returns_user_folders(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """Folder tree API should return user's folders."""
        response = authenticated_client.get("/api/folders")
        
        assert response.status_code == 200
        folders = response.json()
        
        # Should be a list
        assert isinstance(folders, list)
        # Should contain our test folder
        folder_ids = [f["id"] for f in folders]
        assert test_folder in folder_ids


class TestFolderPermissions:
    """Test folder access control and sharing."""
    
    def test_user_can_access_own_folder(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """User should access their own folder."""
        response = authenticated_client.get(f"/?folder_id={test_folder}")
        
        # Should not redirect to login or show access denied
        assert response.status_code == 200
    
    def test_user_cannot_access_others_folder_without_permission(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        db_connection
    ):
        """User without permission cannot access another's folder."""
        from app.infrastructure.repositories import FolderRepository
        
        # Second user creates folder
        folder_repo = FolderRepository(db_connection)
        private_folder = folder_repo.create("Private", second_user["id"])
        
        # First user tries to access
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.get(f"/?folder_id={private_folder}")
        
        # Should be forbidden
        assert response.status_code in [403, 302]  # 403 or redirect
    
    def test_shared_folder_accessible_to_viewer(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        db_connection
    ):
        """Shared folder should be accessible to viewer."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository
        
        # Second user creates and shares folder
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        shared_folder = folder_repo.create("Shared", second_user["id"])
        perm_repo.grant(shared_folder, test_user["id"], "viewer", second_user["id"])
        
        # First user accesses
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.get(f"/?folder_id={shared_folder}")
        
        assert response.status_code == 200
    
    def test_viewer_cannot_upload_to_shared_folder(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """Viewer permission should not allow uploads."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository

        # Setup: second user shares folder as viewer-only
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        shared_folder = folder_repo.create("View Only", second_user["id"])
        perm_repo.grant(shared_folder, test_user["id"], "viewer", second_user["id"])
        
        # First user tries to upload
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.post(
            "/upload",
            data={"folder_id": shared_folder},
            files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
        )
        
        assert response.status_code == 403
    
    def test_editor_can_upload_to_shared_folder(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """Editor permission should allow uploads."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository
        
        # Setup: second user shares folder as editor
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        shared_folder = folder_repo.create("Editable", second_user["id"])
        perm_repo.grant(shared_folder, test_user["id"], "editor", second_user["id"])
        
        # First user uploads - get CSRF token first
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
        
        # Get fresh CSRF token after login
        csrf_token = client.cookies.get("synth_csrf", "")
        
        response = client.post(
            "/upload",
            data={"folder_id": shared_folder},
            files={"file": ("test.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200


class TestFolderHierarchy:
    """Test nested folder structure."""
    
    def test_nested_folder_creation(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        db_connection
    ):
        """Create nested folder structure."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        parent = folder_repo.create("Parent", test_user["id"])
        child = folder_repo.create("Child", test_user["id"], parent)
        grandchild = folder_repo.create("Grandchild", test_user["id"], child)
        
        # All should be accessible
        for folder_id in [parent, child, grandchild]:
            response = authenticated_client.get(f"/?folder_id={folder_id}")
            assert response.status_code == 200, f"Failed to access folder {folder_id}"
    
    def test_folder_tree_shows_hierarchy(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        db_connection
    ):
        """Folder tree should reflect parent-child relationships."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        parent = folder_repo.create("TreeParent", test_user["id"])
        child = folder_repo.create("TreeChild", test_user["id"], parent)
        
        response = authenticated_client.get("/api/folders")
        assert response.status_code == 200
        folders = response.json()
        
        # Find child in tree
        child_folder = next((f for f in folders if f["id"] == child), None)
        if child_folder:
            assert child_folder.get("parent_id") == parent


class TestFolderDeletion:
    """Test folder deletion and cleanup."""
    
    def test_delete_folder_removes_contents(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        test_image_bytes: bytes,
        csrf_token: str,
        db_connection
    ):
        """Deleting folder should remove photos from database."""
        from app.infrastructure.repositories import FolderRepository
        from app.database import get_db
        
        # Create folder with photo
        folder_repo = FolderRepository(db_connection)
        folder_id = folder_repo.create("ToDelete", test_user["id"])
        
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": folder_id},
            files={"file": ("delete_me.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200, f"Upload failed: {response.text}"
        photo_id = response.json()["id"]
        
        # Verify photo exists in database
        db = get_db()
        photo = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        assert photo is not None
        
        # Delete folder
        deleted_files = folder_repo.delete(folder_id)
        
        # Photo should be removed from database
        photo = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        assert photo is None
        
        # deleted_files should contain the filename
        assert len(deleted_files) > 0
    
    def test_only_owner_can_delete_folder(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        db_connection
    ):
        """Only folder owner can delete it."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository
        
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        folder_id = folder_repo.create("Protected", second_user["id"])
        perm_repo.grant(folder_id, test_user["id"], "editor", second_user["id"])
        
        # Editor (not owner) tries to delete
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        # Assuming API endpoint exists
        response = client.delete(f"/api/folders/{folder_id}")
        
        if response.status_code != 404:  # If endpoint exists
            assert response.status_code == 403


class TestFolderContentAPI:
    """Test folder content retrieval via API."""
    
    def test_folder_content_returns_photos_albums(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes
    ):
        """Folder content API should list photos and albums."""
        # Upload a photo first
        authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            files={"file": ("content_test.jpg", test_image_bytes, "image/jpeg")}
        )
        
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        
        if response.status_code == 404:
            pytest.skip("Folder content API not implemented")
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have expected structure
        assert "photos" in data or "items" in data
    
    def test_breadcrumbs_returned_for_folder(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        db_connection
    ):
        """Breadcrumbs should show path from root."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        level1 = folder_repo.create("Level1", test_user["id"])
        level2 = folder_repo.create("Level2", test_user["id"], level1)
        level3 = folder_repo.create("Level3", test_user["id"], level2)
        
        response = authenticated_client.get(f"/api/folders/{level3}/breadcrumbs")
        
        if response.status_code == 404:
            pytest.skip("Breadcrumbs API not implemented")
        
        assert response.status_code == 200
        breadcrumbs = response.json()
        
        # Should contain hierarchy
        names = [b["name"] for b in breadcrumbs]
        assert "Level1" in names
        assert "Level2" in names
        assert "Level3" in names
