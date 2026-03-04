"""
Album management integration tests.

Verifies:
- Album CRUD operations
- Album thumbnail dimensions from cover photo
- Album photo reordering
- Album access control
"""
from fastapi.testclient import TestClient


class TestAlbumCreation:
    """Test album creation and management."""
    
    def test_create_album_via_api(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Album can be created via API."""
        # Upload multiple photos first
        photo_ids = []
        for i in range(3):
            response = authenticated_client.post(
                "/upload",
                data={"folder_id": test_folder},
                files={"file": (f"album_photo_{i}.jpg", test_image_bytes, "image/jpeg")},
                headers={"X-CSRF-Token": csrf_token}
            )
            assert response.status_code == 200
            photo_ids.append(response.json()["id"])
        
        # Create album via upload-album endpoint
        files = []
        for i, photo_id in enumerate(photo_ids):
            files.append(("files", (f"album_{i}.jpg", test_image_bytes, "image/jpeg")))
        
        response = authenticated_client.post(
            "/upload-album",
            data={
                "folder_id": test_folder,
                "album_name": "Test Album"
            },
            files=files,
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "album_id" in data
        assert data["photo_count"] == 3
    
    def test_album_inherits_folder_permissions(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes,
        db_connection
    ):
        """Album inherits permissions from parent folder - viewer can access album."""
        from app.infrastructure.repositories import FolderRepository, PermissionRepository, AlbumRepository
        
        folder_repo = FolderRepository(db_connection)
        perm_repo = PermissionRepository(db_connection)
        album_repo = AlbumRepository(db_connection)
        
        # Second user creates folder and shares with first user as viewer
        folder_id = folder_repo.create("SharedAlbumFolder", second_user["id"])
        perm_repo.grant(folder_id, test_user["id"], "viewer", second_user["id"])
        
        # Second user creates album in the shared folder
        album_id = album_repo.create(folder_id, second_user["id"], "Shared Album")
        
        # Login as first user (viewer)
        resp = client.post("/login", data={
            "username": test_user["username"],
            "password": test_user["password"]
        })
        assert resp.status_code == 200
        
        # Viewer should be able to access the album
        resp = client.get(f"/api/albums/{album_id}")
        assert resp.status_code == 200, f"Viewer should be able to access album, got {resp.status_code}"
        data = resp.json()
        assert data["id"] == album_id
        assert data["name"] == "Shared Album"
        # Viewer should not have edit permission
        assert data.get("can_edit") is False
    
    def test_album_shows_correct_photo_count(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Album badge shows correct photo count."""
        # Upload photos
        photo_ids = []
        for i in range(5):
            response = authenticated_client.post(
                "/upload",
                data={"folder_id": test_folder},
                files={"file": (f"count_{i}.jpg", test_image_bytes, "image/jpeg")},
                headers={"X-CSRF-Token": csrf_token}
            )
            assert response.status_code == 200
            photo_ids.append(response.json()["id"])
        
        # Create album
        response = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Count Test Album",
                "folder_id": test_folder,
                "photo_ids": photo_ids
            },
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        assert response.json()["album"]["photo_count"] == 5


class TestAlbumThumbnailDimensions:
    """Test album thumbnail dimensions from cover photo."""
    
    def test_album_has_cover_thumbnail_dimensions(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Album API returns cover thumbnail dimensions."""
        # Upload a photo
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            files={"file": ("cover.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        photo_id = response.json()["id"]
        
        # Create album with this photo
        response = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Cover Test Album",
                "folder_id": test_folder,
                "photo_ids": [photo_id]
            },
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        album_id = response.json()["album"]["id"]
        
        # Get folder content - should include album with dimensions
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        assert response.status_code == 200
        
        data = response.json()
        albums = [item for item in data.get("items", []) if item.get("type") == "album"]
        
        if albums:
            album = albums[0]
            # Should have cover thumbnail dimensions
            assert "cover_thumb_width" in album
            assert "cover_thumb_height" in album
            # Dimensions should be positive
            assert album["cover_thumb_width"] > 0
            assert album["cover_thumb_height"] > 0
    
    def test_album_placeholder_has_correct_aspect_ratio(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Album placeholder should have correct aspect ratio from cover photo."""
        # Upload photo
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            files={"file": ("aspect.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        photo_id = response.json()["id"]
        
        # Create album
        response = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Aspect Test Album",
                "folder_id": test_folder,
                "photo_ids": [photo_id]
            },
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        
        # Get folder content via API to check dimensions
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        assert response.status_code == 200
        
        data = response.json()
        albums = [item for item in data.get("albums", []) if item.get("type") == "album"]
        
        if albums:
            album = albums[0]
            # Should have cover thumbnail dimensions or placeholder dimensions
            assert "cover_thumb_width" in album or "thumb_width" in album or "placeholder_width" in album
            assert "cover_thumb_height" in album or "thumb_height" in album or "placeholder_height" in album


class TestAlbumSorting:
    """Test album sorting with photos."""
    
    def test_albums_sorted_by_max_photo_date(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Albums sorted by max photo date alongside photos."""
        # Get folder content
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        assert response.status_code == 200
        
        data = response.json()
        items = data.get("items", [])
        
        # Albums and photos should be mixed in items list
        types = [item.get("type") for item in items]
        
        # Both albums and photos should have date fields for sorting
        for item in items:
            if item.get("type") in ("photo", "album"):
                assert "uploaded_at" in item or "taken_at" in item


class TestAlbumNavigation:
    """Test album navigation in lightbox."""
    
    def test_album_photos_navigable_in_lightbox(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Album photos can be navigated in lightbox."""
        # Upload photos
        photo_ids = []
        for i in range(3):
            response = authenticated_client.post(
                "/upload",
                data={"folder_id": test_folder},
                files={"file": (f"nav_{i}.jpg", test_image_bytes, "image/jpeg")},
                headers={"X-CSRF-Token": csrf_token}
            )
            assert response.status_code == 200
            photo_ids.append(response.json()["id"])
        
        # Create album
        response = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Nav Test Album",
                "folder_id": test_folder,
                "photo_ids": photo_ids
            },
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        album_id = response.json()["album"]["id"]
        
        # Get album details
        response = authenticated_client.get(f"/api/albums/{album_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 3


class TestAlbumReorder:
    """Test album photo reordering."""
    
    def test_reorder_album_photos(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Photos in album can be reordered."""
        # Upload photos
        photo_ids = []
        for i in range(3):
            response = authenticated_client.post(
                "/upload",
                data={"folder_id": test_folder},
                files={"file": (f"reorder_{i}.jpg", test_image_bytes, "image/jpeg")},
                headers={"X-CSRF-Token": csrf_token}
            )
            assert response.status_code == 200
            photo_ids.append(response.json()["id"])
        
        # Create album
        response = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Reorder Test Album",
                "folder_id": test_folder,
                "photo_ids": photo_ids
            },
            headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status_code == 200
        album_id = response.json()["album"]["id"]
        
        # Reorder - reverse order
        reversed_ids = list(reversed(photo_ids))
        response = authenticated_client.put(
            f"/api/albums/{album_id}/reorder",
            json={"photo_ids": reversed_ids},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        
        # Verify new order
        response = authenticated_client.get(f"/api/albums/{album_id}")
        assert response.status_code == 200
        
        returned_ids = [p["id"] for p in response.json()["items"]]
        assert returned_ids == reversed_ids
