"""
Sort preference persistence tests.

Verifies:
- Sort preference saved per folder
- Sort preference retrieved correctly
- Sort preference defaults to 'uploaded'
"""
from fastapi.testclient import TestClient


class TestSortPreferenceAPI:
    """Test sort preference API endpoints."""
    
    def test_save_sort_preference(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        csrf_token: str
    ):
        """Sort preference can be saved for a folder."""
        response = authenticated_client.put(
            f"/api/folders/{test_folder}/sort",
            json={"sort_by": "taken"},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["sort"] == "taken"
    
    def test_retrieve_saved_sort_preference(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        csrf_token: str
    ):
        """Saved sort preference is returned by folder content API."""
        # Save preference
        authenticated_client.put(
            f"/api/folders/{test_folder}/sort",
            json={"sort_by": "taken"},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Get folder content
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("sort") == "taken"
    
    def test_default_sort_preference_is_uploaded(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        db_connection
    ):
        """Default sort preference is 'uploaded' when not set."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        folder_id = folder_repo.create("NoPrefFolder", test_user["id"])
        
        response = authenticated_client.get(f"/api/folders/{folder_id}/content")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("sort") == "uploaded"
    
    def test_sort_preference_is_per_folder(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        db_connection,
        csrf_token: str
    ):
        """Each folder has its own sort preference."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        folder1 = folder_repo.create("Folder1", test_user["id"])
        folder2 = folder_repo.create("Folder2", test_user["id"])
        
        # Set different preferences
        authenticated_client.put(
            f"/api/folders/{folder1}/sort",
            json={"sort_by": "taken"},
            headers={"X-CSRF-Token": csrf_token}
        )
        authenticated_client.put(
            f"/api/folders/{folder2}/sort",
            json={"sort_by": "uploaded"},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Verify each folder has correct preference
        resp1 = authenticated_client.get(f"/api/folders/{folder1}/content")
        resp2 = authenticated_client.get(f"/api/folders/{folder2}/content")
        
        assert resp1.json().get("sort") == "taken"
        assert resp2.json().get("sort") == "uploaded"
    
    def test_invalid_sort_option_rejected(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        csrf_token: str
    ):
        """Invalid sort option returns error."""
        response = authenticated_client.put(
            f"/api/folders/{test_folder}/sort",
            json={"sort_by": "invalid_sort"},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 400
    
    def test_sort_preference_requires_access(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        db_connection
    ):
        """Cannot set sort preference for folder without access."""
        from app.infrastructure.repositories import FolderRepository
        
        folder_repo = FolderRepository(db_connection)
        private_folder = folder_repo.create("PrivateSortFolder", second_user["id"])
        
        # Login as first user
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
        
        # Try to set preference on private folder
        response = client.put(
            f"/api/folders/{private_folder}/sort",
            json={"sort_by": "taken"},
            headers={"X-CSRF-Token": client.cookies.get('synth_csrf', '')}
        )
        
        assert response.status_code == 403
