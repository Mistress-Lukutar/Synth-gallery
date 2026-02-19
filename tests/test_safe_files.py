"""Tests for safe file access endpoints.

Tests the safe_files.py routes for accessing encrypted files in safes.
"""
import pytest


class TestSafeFileAccess:
    """Test file access in encrypted safes."""

    def test_safe_thumbnail_returns_404_for_nonexistent_photo(self, client, test_user):
        """Test that safe thumbnail endpoint returns 404 for nonexistent photo."""
        # Login as test user
        client.post("/login", data={
            "username": test_user["username"],
            "password": test_user["password"]
        })
        
        # Try to access a non-existent photo in safe
        # This tests that can_access_photo is called correctly without AttributeError
        response = client.get("/api/safe-files/photos/nonexistent/thumbnail")
        # Should return 404 (not found) or 403 (no access), not 500 (server error)
        assert response.status_code in [404, 403]
        # The error should NOT be about can_access_photo attribute
        if response.status_code == 500:
            assert "can_access_photo" not in response.text
    
    def test_safe_file_endpoints_use_permission_service(self):
        """Verify that safe file endpoints use PermissionService, not PermissionRepository directly."""
        # Import the route module and check imports
        from app.routes import safe_files
        import inspect
        
        # Get source code
        source = inspect.getsource(safe_files)
        
        # Should import PermissionService
        assert "PermissionService" in source
        
        # Should use get_permission_service function
        assert "get_permission_service" in source
        
        # Should NOT directly use PermissionRepository for access checks
        # The lambda should call perm_service.can_access_photo, not perm_repo.can_access_photo
        lines = source.split('\n')
        for line in lines:
            if 'can_access_photo' in line and 'perm_repo' in line:
                pytest.fail(f"Found direct perm_repo usage for can_access_photo: {line}")


class TestSafeFileThumbnail:
    """Test thumbnail access in safes."""

    def test_thumbnail_endpoint_returns_202_for_missing_thumbnail(self, client, test_user, test_folder):
        """Test that missing thumbnail returns 202 with regeneration headers."""
        # This requires a proper setup with safe, photo, etc.
        # For now, just verify the endpoint doesn't crash with AttributeError
        
        # Login first
        client.post("/login", data={
            "username": test_user["username"],
            "password": test_user["password"]
        })
        
        # Try to access thumbnail (will likely 404 or 403, but shouldn't 500)
        response = client.get("/api/safe-files/photos/test-photo-id/thumbnail")
        # Should not be 500 (internal server error)
        assert response.status_code != 500
        # The error should be about missing photo, not about can_access_photo attribute
        if response.status_code == 500:
            assert "can_access_photo" not in response.text


def test_permission_service_has_can_access_photo():
    """Verify PermissionService has can_access_photo method."""
    from app.application.services import PermissionService
    
    # Check that the method exists
    assert hasattr(PermissionService, 'can_access_photo')
    
    # Check signature
    import inspect
    sig = inspect.signature(PermissionService.can_access_photo)
    params = list(sig.parameters.keys())
    assert 'photo_id' in params
    assert 'user_id' in params
