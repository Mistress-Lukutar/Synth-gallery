"""Unit tests for SessionRepository DEK storage."""
import pytest

from app.infrastructure.repositories import SessionRepository, UserRepository


class TestSessionRepositoryDEK:
    """Test suite for session repository DEK operations."""

    def test_create_session_with_encrypted_dek(self, db_connection):
        """Create session with encrypted_dek."""
        # Create user first
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_dek", "password123", "Test User DEK")
        
        session_repo = SessionRepository(db_connection)
        encrypted_dek = b"encrypted-data-here"
        
        session_id = session_repo.create(user_id, encrypted_dek=encrypted_dek)
        
        assert session_id is not None
        session = session_repo.get_valid(session_id)
        assert session["encrypted_dek"] == encrypted_dek

    def test_create_session_without_encrypted_dek(self, db_connection):
        """Create session without encrypted_dek (backward compatibility)."""
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_no_dek", "password123", "Test User No DEK")
        
        session_repo = SessionRepository(db_connection)
        session_id = session_repo.create(user_id)
        
        session = session_repo.get_valid(session_id)
        assert session["encrypted_dek"] is None

    def test_set_encrypted_dek(self, db_connection):
        """Set encrypted_dek for existing session."""
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_set", "password123", "Test User Set")
        
        session_repo = SessionRepository(db_connection)
        session_id = session_repo.create(user_id)
        encrypted_dek = b"new-encrypted-dek"
        
        result = session_repo.set_encrypted_dek(session_id, encrypted_dek)
        
        assert result is True
        session = session_repo.get_valid(session_id)
        assert session["encrypted_dek"] == encrypted_dek

    def test_set_encrypted_dek_invalid_session(self, db_connection):
        """Set encrypted_dek for non-existent session."""
        session_repo = SessionRepository(db_connection)
        result = session_repo.set_encrypted_dek("invalid-session", b"data")
        
        assert result is False

    def test_get_encrypted_dek(self, db_connection):
        """Get encrypted_dek from session."""
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_get", "password123", "Test User Get")
        
        session_repo = SessionRepository(db_connection)
        encrypted_dek = b"test-encrypted-dek"
        session_id = session_repo.create(user_id, encrypted_dek=encrypted_dek)
        
        retrieved = session_repo.get_encrypted_dek(session_id)
        
        assert retrieved == encrypted_dek

    def test_get_encrypted_dek_not_set(self, db_connection):
        """Get encrypted_dek when not set."""
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_notset", "password123", "Test User Not Set")
        
        session_repo = SessionRepository(db_connection)
        session_id = session_repo.create(user_id)
        
        retrieved = session_repo.get_encrypted_dek(session_id)
        
        assert retrieved is None

    def test_get_encrypted_dek_invalid_session(self, db_connection):
        """Get encrypted_dek for non-existent session."""
        session_repo = SessionRepository(db_connection)
        retrieved = session_repo.get_encrypted_dek("invalid-session")
        
        assert retrieved is None

    def test_session_deletion_removes_dek(self, db_connection):
        """Deleting session removes encrypted_dek."""
        user_repo = UserRepository(db_connection)
        user_id = user_repo.create("testuser_del", "password123", "Test User Del")
        
        session_repo = SessionRepository(db_connection)
        encrypted_dek = b"will-be-deleted"
        session_id = session_repo.create(user_id, encrypted_dek=encrypted_dek)
        
        session_repo.delete(session_id)
        
        retrieved = session_repo.get_encrypted_dek(session_id)
        assert retrieved is None
