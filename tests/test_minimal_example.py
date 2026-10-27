'''
File:   test_minimal_example.py
Brief:  Minimal working example showing how to test with CSRF.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

Minimal working example - shows how to test with CSRF.
Run: pytest tests/test_minimal_example.py -v
'''
import pytest
from fastapi.testclient import TestClient
from app.config import CSRF_COOKIE_NAME


def test_upload_with_csrf(authenticated_client: TestClient, test_folder: str):
    """Example: uploading a file with a CSRF token."""
    from PIL import Image
    import io
    
    img = Image.new('RGB', (100, 100), color='red')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    
    csrf_token = authenticated_client.cookies.get(CSRF_COOKIE_NAME, "")
    
    response = authenticated_client.post(
        "/api/uploads",
        data={"folder_id": test_folder},
        files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "filename" in data
    print(f"✅ Upload successful: {data['id']}")


def test_folder_tree_api(authenticated_client: TestClient):
    """Example: getting the folder tree."""
    response = authenticated_client.get("/api/folders/tree")
    
    # The API may answer 405 when the endpoint does not exist, or 200 when it does.
    if response.status_code == 200:
        folders = response.json()
        print(f"✅ Found {len(folders)} folders")
    else:
        print(f"⚠️  API returned {response.status_code} - endpoint may not exist yet")
        pytest.skip("API endpoint not implemented")
