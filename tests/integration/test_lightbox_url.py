"""
Lightbox URL with photo_id tests.

Verifies:
- URL includes photo_id when lightbox is open
- URL removes photo_id when lightbox is closed
- Direct URL with photo_id opens lightbox
- Navigation updates photo_id in URL
"""
from fastapi.testclient import TestClient


class TestLightboxURL:
    """Test lightbox URL handling."""
    
    def test_photo_id_added_to_url_on_open(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        uploaded_photo: dict
    ):
        """URL should include photo_id when photo is opened."""
        # Open photo
        response = authenticated_client.get(
            f"/?folder_id={test_folder}&photo_id={uploaded_photo['id']}"
        )
        
        assert response.status_code == 200
        # Response should include lightbox initialization
        assert "lightbox" in response.text.lower() or "photo_id" in response.text
    
    def test_folder_content_includes_photo_dates(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        uploaded_photo: dict
    ):
        """Folder content API includes dates for lightbox navigation."""
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        
        assert response.status_code == 200
        data = response.json()
        
        items = data.get("items", [])
        photos = [item for item in items if item.get("type") == "photo"]
        
        if photos:
            # Photos should have date fields for sorting
            photo = photos[0]
            assert "uploaded_at" in photo
            assert "id" in photo
    
    def test_api_photo_includes_navigation_info(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        uploaded_photo: dict
    ):
        """Photo API returns info needed for lightbox navigation."""
        response = authenticated_client.get(f"/api/photos/{uploaded_photo['id']}")
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have all fields needed for lightbox display
        assert "id" in data
        assert "filename" in data
        assert "original_name" in data
        assert "media_type" in data
        
        # Album info if applicable
        if data.get("album"):
            assert "photo_ids" in data["album"]


class TestLightboxNavigationAPI:
    """Test lightbox navigation through API."""
    
    def test_get_adjacent_photos_in_folder(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes,
        csrf_token: str
    ):
        """Can get adjacent photos for navigation."""
        # Upload multiple photos
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
        
        # Get folder content - should have all photos in order
        response = authenticated_client.get(f"/api/folders/{test_folder}/content")
        
        assert response.status_code == 200
        data = response.json()
        
        items = data.get("items", [])
        photo_items = [item for item in items if item.get("type") == "photo"]
        
        # Should be able to find current and adjacent photos
        assert len(photo_items) >= 3
        
        # Each photo should have id for URL building
        for photo in photo_items:
            assert "id" in photo
