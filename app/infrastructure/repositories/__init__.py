# Repository Pattern Implementation
"""
Repositories abstract database operations.
Each entity has its own repository.

Migration Guide:
1. Old: from app.database import get_user_by_id
   New: from app.infrastructure.repositories import UserRepository

2. Old: get_user_by_id(user_id)
   New: repo = UserRepository(db); repo.get_by_id(user_id)
"""
from .base import Repository, ConnectionProtocol, AsyncRepository, AsyncConnectionProtocol
from .user_repository import UserRepository, AsyncUserRepository
from .session_repository import SessionRepository, AsyncSessionRepository
from .folder_repository import FolderRepository, AsyncFolderRepository
from .permission_repository import PermissionRepository, AsyncPermissionRepository
from .photo_repository import PhotoRepository, AsyncPhotoRepository
from .safe_repository import SafeRepository, AsyncSafeRepository
from .webauthn_repository import WebAuthnRepository, AsyncWebAuthnRepository

__all__ = [
    "Repository",
    "ConnectionProtocol",
    "AsyncRepository",
    "AsyncConnectionProtocol",
    "UserRepository",
    "AsyncUserRepository",
    "SessionRepository",
    "AsyncSessionRepository",
    "FolderRepository",
    "AsyncFolderRepository",
    "PermissionRepository",
    "AsyncPermissionRepository",
    "PhotoRepository",
    "AsyncPhotoRepository",
    "SafeRepository",
    "AsyncSafeRepository",
    "WebAuthnRepository",
    "AsyncWebAuthnRepository",
]
