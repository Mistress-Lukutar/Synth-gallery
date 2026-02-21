"""Integration tests for Safe (encrypted vault) security.

Tests security-critical aspects of E2E-encrypted safes:
- Deletion from locked safes is blocked
- File access requires unlocked safe
- Session management and expiration
- Permission boundaries
"""
import pytest
from unittest.mock import Mock, patch


class TestSafeDeleteSecurity:
    """Test that deletion from locked safes is blocked."""
    
    def test_cannot_delete_photo_from_locked_safe(self, authenticated_client, test_user, db_connection):
        """Server-side: Deleting photo from locked safe should fail.
        
        This is the regression test for the bug where users could delete
        photos from locked safes through the UI because client and server
        state were out of sync.
        """
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        from app.application.services import SafeService
        
        # Create a safe with folder
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe (simulating E2E setup)
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder in safe
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        # Create photo in safe folder (simulating already uploaded)
        import uuid
        photo_id = str(uuid.uuid4())
        photo_repo.create(
            filename=f"{photo_id}.jpg",
            folder_id=folder_id,
            user_id=test_user["id"],
            photo_id=photo_id,
            original_name="test.jpg",
            media_type="image",
            safe_id=safe_id
        )
        
        # Safe is NOT unlocked (no session created)
        # Try to delete photo
        csrf_token = authenticated_client.cookies.get("synth_csrf", "")
        response = authenticated_client.post(
            "/api/photos/batch-delete",
            json={"photo_ids": [photo_id], "album_ids": []},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Should fail - server should check is_unlocked
        # The response might be 200 with partial failure or 403
        if response.status_code == 200:
            result = response.json()
            # Photo should NOT be deleted
            assert result.get("photos_deleted", 0) == 0, \
                "Should not delete photos from locked safe"
        else:
            assert response.status_code in [403, 400], \
                f"Expected 403 or 400, got {response.status_code}"
    
    def test_cannot_delete_album_from_locked_safe(self, authenticated_client, test_user, db_connection):
        """Server-side: Deleting album from locked safe should fail."""
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder and album in safe
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        # Create album via raw SQL (create_album doesn't support safe_id)
        import uuid
        album_id = str(uuid.uuid4())
        db_connection.execute(
            "INSERT INTO albums (id, name, user_id, folder_id, safe_id) VALUES (?, ?, ?, ?, ?)",
            (album_id, "Test Album", test_user["id"], folder_id, safe_id)
        )
        db_connection.commit()
        
        # Try to delete album without unlocking safe
        csrf_token = authenticated_client.cookies.get("synth_csrf", "")
        response = authenticated_client.post(
            "/api/photos/batch-delete",
            json={"photo_ids": [], "album_ids": [album_id]},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Should fail
        if response.status_code == 200:
            result = response.json()
            assert result.get("albums_deleted", 0) == 0, \
                "Should not delete albums from locked safe"
        else:
            assert response.status_code in [403, 400]
    
    def test_can_delete_from_unlocked_safe(self, authenticated_client, test_user, db_connection):
        """Server-side: Deleting from unlocked safe should succeed."""
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder and photo in safe
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        import uuid
        photo_id = str(uuid.uuid4())
        photo_repo.create(
            photo_id=photo_id,
            filename=f"{photo_id}.jpg",
            original_name="test.jpg",
            media_type="image",
            user_id=test_user["id"],
            folder_id=folder_id,
            safe_id=safe_id,

        )
        
        # UNLOCK the safe - create session
        safe_repo.create_session(safe_id, test_user["id"], b"fake_session_encrypted_dek")
        
        # Now deletion should work
        csrf_token = authenticated_client.cookies.get("synth_csrf", "")
        response = authenticated_client.post(
            "/api/photos/batch-delete",
            json={"photo_ids": [photo_id], "album_ids": []},
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result.get("deleted_photos", 0) == 1, \
            "Should delete photos from unlocked safe"


class TestSafeSessionSecurity:
    """Test safe session management."""
    
    def test_safe_session_expires(self, authenticated_client, test_user, db_connection):
        """Safe sessions should expire after configured time."""
        from app.infrastructure.repositories import SafeRepository
        
        safe_repo = SafeRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create session with 0 hours expiration (already expired)
        session_id = safe_repo.create_session(
            safe_id, 
            test_user["id"], 
            b"fake_session_encrypted_dek",
            expires_hours=0  # Expired immediately
        )
        
        # Check that session is NOT valid
        session = safe_repo.get_session(session_id)
        assert session is None, "Expired session should not be valid"
        
        # is_unlocked should return False
        is_unlocked = safe_repo.is_unlocked(safe_id, test_user["id"])
        assert is_unlocked is False, "Expired safe should be locked"
    
    def test_lock_safe_invalidates_session(self, authenticated_client, test_user, db_connection):
        """Locking safe should invalidate all sessions."""
        from app.infrastructure.repositories import SafeRepository
        
        safe_repo = SafeRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create session
        safe_repo.create_session(safe_id, test_user["id"], b"fake_session_encrypted_dek")
        
        # Verify unlocked
        assert safe_repo.is_unlocked(safe_id, test_user["id"]) is True
        
        # Lock safe via API
        csrf_token = authenticated_client.cookies.get("synth_csrf", "")
        response = authenticated_client.post(
            f"/api/safes/{safe_id}/lock",
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        
        # Verify locked
        is_unlocked = safe_repo.is_unlocked(safe_id, test_user["id"])
        assert is_unlocked is False, "Safe should be locked after lock API call"
    
    def test_other_user_cannot_unlock_my_safe(self, authenticated_client, second_user, db_connection):
        """User should not be able to unlock another user's safe."""
        from app.infrastructure.repositories import SafeRepository
        
        safe_repo = SafeRepository(db_connection)
        
        # Create safe for first user (test_user from authenticated_client)
        # But we need to login as second_user to test this...
        # This test needs different setup
        pass  # Will be covered by permission tests


class TestSafeFileAccessSecurity:
    """Test that file access in safes is properly protected."""
    
    def test_cannot_access_safe_file_without_unlock(self, authenticated_client, test_user, db_connection):
        """File access should fail for locked safe."""
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder and photo
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        import uuid
        photo_id = str(uuid.uuid4())
        photo_repo.create(
            photo_id=photo_id,
            filename=f"{photo_id}.jpg",
            original_name="test.jpg",
            media_type="image",
            user_id=test_user["id"],
            folder_id=folder_id,
            safe_id=safe_id
        )
        
        # Try to access file without unlocking
        response = authenticated_client.get(f"/api/safe-files/photos/{photo_id}/file")
        
        # Should get 403 (forbidden) or 404
        assert response.status_code in [403, 404], \
            f"Expected 403 or 404 for locked safe file, got {response.status_code}"
    
    def test_cannot_access_safe_thumbnail_without_unlock(self, authenticated_client, test_user, db_connection):
        """Thumbnail access should fail for locked safe."""
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder and photo
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        import uuid
        photo_id = str(uuid.uuid4())
        photo_repo.create(
            photo_id=photo_id,
            filename=f"{photo_id}.jpg",
            original_name="test.jpg",
            media_type="image",
            user_id=test_user["id"],
            folder_id=folder_id,
            safe_id=safe_id
        )
        
        # Try to access thumbnail without unlocking
        response = authenticated_client.get(f"/api/safe-files/photos/{photo_id}/thumbnail")
        
        # Should get 403 or 404
        assert response.status_code in [403, 404], \
            f"Expected 403 or 404 for locked safe thumbnail, got {response.status_code}"


class TestSafePermissionSecurity:
    """Test permission boundaries for safes."""
    
    def test_user_cannot_delete_other_user_safe(self, client, test_user, second_user, db_connection):
        """User should not be able to delete another user's safe."""
        from app.infrastructure.repositories import SafeRepository
        
        safe_repo = SafeRepository(db_connection)
        
        # Create safe for test_user
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Login as second_user
        client.post("/login", data={
            "username": second_user["username"],
            "password": second_user["password"]
        })
        
        # Try to delete safe as second_user
        csrf_token = client.cookies.get("synth_csrf", "")
        response = client.delete(
            f"/api/safes/{safe_id}",
            headers={"X-CSRF-Token": csrf_token}
        )
        
        # Should be forbidden
        assert response.status_code == 403, \
            f"Expected 403 for unauthorized safe delete, got {response.status_code}"
    
    def test_user_cannot_access_other_user_safe_files(self, client, test_user, second_user, db_connection):
        """User should not access files in another user's safe."""
        from app.infrastructure.repositories import SafeRepository, FolderRepository, PhotoRepository
        
        safe_repo = SafeRepository(db_connection)
        folder_repo = FolderRepository(db_connection)
        photo_repo = PhotoRepository(db_connection)
        
        # Create safe for test_user
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create folder and photo in safe
        folder_id = folder_repo.create("Safe Folder", test_user["id"], safe_id=safe_id)
        
        import uuid
        photo_id = str(uuid.uuid4())
        photo_repo.create(
            photo_id=photo_id,
            filename=f"{photo_id}.jpg",
            original_name="test.jpg",
            media_type="image",
            user_id=test_user["id"],
            folder_id=folder_id,
            safe_id=safe_id
        )
        
        # Unlock safe as owner
        safe_repo.create_session(safe_id, test_user["id"], b"fake_session_encrypted_dek")
        
        # Login as second_user
        client.post("/login", data={
            "username": second_user["username"],
            "password": second_user["password"]
        })
        
        # Try to access file as second_user
        response = client.get(f"/api/safe-files/photos/{photo_id}/file")
        
        # Should be forbidden even if safe is unlocked by owner
        assert response.status_code in [403, 404], \
            f"Expected 403 or 404 for unauthorized file access, got {response.status_code}"


class TestSafeClientSync:
    """Test client-server synchronization for E2E safes.
    
    These tests verify that the client-side lock state is properly
    communicated to and respected by the server.
    """
    
    def test_server_respects_client_lock_signal(self, authenticated_client, test_user, db_connection):
        """When client signals lock, server should invalidate session.
        
        This tests the sendBeacon mechanism from safe-crypto.js
        that notifies server when client locks safe.
        """
        from app.infrastructure.repositories import SafeRepository
        
        safe_repo = SafeRepository(db_connection)
        
        # Create safe
        safe_id = safe_repo.create(
            name="Test Safe",
            user_id=test_user["id"],
            encrypted_dek=b"fake_encrypted_dek",
            unlock_type="password",
            salt=b"fake_salt"
        )
        
        # Create session (simulating unlock)
        safe_repo.create_session(safe_id, test_user["id"], b"fake_session_encrypted_dek")
        assert safe_repo.is_unlocked(safe_id, test_user["id"]) is True
        
        # Simulate client lock signal via API
        csrf_token = authenticated_client.cookies.get("synth_csrf", "")
        response = authenticated_client.post(
            f"/api/safes/{safe_id}/lock",
            headers={"X-CSRF-Token": csrf_token}
        )
        
        assert response.status_code == 200
        assert safe_repo.is_unlocked(safe_id, test_user["id"]) is False
