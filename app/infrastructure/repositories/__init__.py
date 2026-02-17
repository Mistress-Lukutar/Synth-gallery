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
from .base import Repository, ConnectionProtocol
from .user_repository import UserRepository
from .session_repository import SessionRepository
from .folder_repository import FolderRepository

__all__ = [
    "Repository",
    "ConnectionProtocol",
    "UserRepository",
    "SessionRepository",
    "FolderRepository",
]
