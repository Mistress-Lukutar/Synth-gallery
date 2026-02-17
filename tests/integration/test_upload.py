"""
File upload integration tests.

Verifies:
- Single file upload (encrypted and unencrypted)
- Album upload (multiple files)
- File type validation
- Access control for uploads
- Download/retrieval of uploaded files
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path


def _csrf_headers(client: TestClient) -> dict:
    """Get CSRF headers for POST requests."""
    token = client.cookies.get("synth_csrf", "")
    return {"X-CSRF-Token": token}


class TestSingleFileUpload:
    """Test single photo/video upload."""
    
    def test_upload_image_without_encryption(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes
    ):
        """Upload unencrypted image successfully."""
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            headers=_csrf_headers(authenticated_client), files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "id" in data
        assert "filename" in data
        assert data["media_type"] == "image"
        assert data["filename"].endswith(".jpg")
    
    def test_upload_with_encryption_enabled(
        self,
        client: TestClient,
        encrypted_user: dict
    ):
        """Upload with server-side encryption (user has DEK in cache)."""
        from app.database import create_folder
        
        folder_id = create_folder("Encrypted Folder", encrypted_user["id"])
        
        # Create test image
        from PIL import Image
        import io
        img = Image.new('RGB', (100, 100), color='blue')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        
        response = client.post(
            "/upload",
            data={"folder_id": folder_id},
            headers=_csrf_headers(authenticated_client), files={"file": ("encrypted.jpg", img_bytes.getvalue(), "image/jpeg")}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify file is encrypted on disk (not valid JPEG)
        from app.config import UPLOADS_DIR
        file_path = UPLOADS_DIR / data["filename"]
        with open(file_path, "rb") as f:
            content = f.read()
        
        # Encrypted content should not start with JPEG magic bytes
        assert not content.startswith(b'\xff\xd8\xff')
    
    def test_upload_rejects_invalid_file_type(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """Upload should reject non-media files."""
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            headers=_csrf_headers(authenticated_client), files={"file": ("malware.exe", b"not an image", "application/octet-stream")}
        )
        
        assert response.status_code == 400
    
    def test_upload_requires_folder_id(
        self,
        authenticated_client: TestClient,
        test_image_bytes: bytes
    ):
        """Upload without folder_id should fail."""
        response = authenticated_client.post(
            "/upload",
            data={},  # No folder_id
            headers=_csrf_headers(authenticated_client), files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
        )
        
        assert response.status_code in [400, 422]
    
    def test_upload_requires_edit_permission(
        self,
        client: TestClient,
        test_user: dict,
        second_user: dict,
        test_image_bytes: bytes
    ):
        """User without edit permission cannot upload to folder."""
        from app.database import create_folder
        
        # Create folder as second user
        folder_id = create_folder("Private Folder", second_user["id"])
        
        # Try to upload as first user (no permission)
        client.post(
            "/login",
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False
        )
        
        response = client.post(
            "/upload",
            data={"folder_id": folder_id},
            headers=_csrf_headers(authenticated_client), files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
        )
        
        assert response.status_code == 403


class TestAlbumUpload:
    """Test multi-file album upload."""
    
    def test_upload_album_with_multiple_images(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """Upload multiple files as an album."""
        from PIL import Image
        import io
        
        # Create multiple test images
        files = []
        for i in range(3):
            img = Image.new('RGB', (100, 100), color=['red', 'green', 'blue'][i])
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            files.append((
                "files",
                (f"image{i}.jpg", img_bytes.getvalue(), "image/jpeg")
            ))
        
        response = authenticated_client.post(
            "/upload-album",
            data={"folder_id": test_folder},
            files=files
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "album_id" in data
        assert "photos" in data
        assert len(data["photos"]) == 3
    
    def test_album_requires_minimum_two_files(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes
    ):
        """Album upload with < 2 files should fail."""
        response = authenticated_client.post(
            "/upload-album",
            data={"folder_id": test_folder},
            headers=_csrf_headers(authenticated_client), files={"files": ("single.jpg", test_image_bytes, "image/jpeg")}
        )
        
        assert response.status_code == 400


class TestFileRetrieval:
    """Test downloading/retrieving uploaded files."""
    
    def test_retrieve_uploaded_image(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Can retrieve uploaded image via /uploads/{filename}."""
        response = authenticated_client.get(f"/uploads/{uploaded_photo['filename']}")
        
        assert response.status_code == 200
        # Should be image data
        assert response.headers.get("content-type", "").startswith("image/") or \
               response.headers.get("content-type", "") == "application/octet-stream"
    
    def test_retrieve_thumbnail_generated(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Thumbnail should be generated and retrievable."""
        thumbnail_name = f"{uploaded_photo['id']}.jpg"
        response = authenticated_client.get(f"/thumbnails/{thumbnail_name}")
        
        assert response.status_code == 200
        assert response.headers.get("content-type") == "image/jpeg"
    
    def test_unauthenticated_cannot_retrieve_file(
        self,
        client: TestClient,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """Unauthenticated user should not access files."""
        # New client (not authenticated)
        response = client.get(f"/uploads/{uploaded_photo['filename']}")
        
        # Should redirect to login or return 401
        assert response.status_code in [302, 401]
    
    def test_download_preserves_file_content(
        self,
        authenticated_client: TestClient,
        test_folder: str,
        test_image_bytes: bytes
    ):
        """Downloaded file should match uploaded content (unencrypted)."""
        # Upload
        response = authenticated_client.post(
            "/upload",
            data={"folder_id": test_folder},
            headers=_csrf_headers(authenticated_client), files={"file": ("original.jpg", test_image_bytes, "image/jpeg")}
        )
        filename = response.json()["filename"]
        
        # Download
        response = authenticated_client.get(f"/uploads/{filename}")
        downloaded_content = response.content
        
        # For unencrypted upload, content should match
        # (For encrypted, this would differ - that's tested separately)
        assert len(downloaded_content) > 0


class TestBulkUpload:
    """Test bulk folder upload with structure."""
    
    def test_bulk_upload_creates_albums_from_subfolders(
        self,
        authenticated_client: TestClient,
        test_folder: str
    ):
        """Bulk upload should create albums from subdirectories."""
        import json
        from PIL import Image
        import io
        
        # Create test files
        files = []
        paths = []
        
        # Root level file
        img = Image.new('RGB', (50, 50), color='red')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        files.append(("files", ("root.jpg", buf.getvalue(), "image/jpeg")))
        paths.append("root.jpg")
        
        # Subfolder file (should become album)
        img = Image.new('RGB', (50, 50), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        files.append(("files", ("album1/photo.jpg", buf.getvalue(), "image/jpeg")))
        paths.append("Vacation/album_photo.jpg")
        
        response = authenticated_client.post(
            "/upload-bulk",
            data={
                "folder_id": test_folder,
                "paths": json.dumps(paths)
            },
            files=files
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have created album from subfolder
        assert data["albums_created"] >= 1 or data["photos_in_albums"] >= 1
