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
from .base import Repository
from .user_repository import UserRepository
from .session_repository import SessionRepository
from .folder_repository import FolderRepository
from .permission_repository import PermissionRepository

from .safe_repository import SafeRepository
from .webauthn_repository import WebAuthnRepository
from .item_repository import ItemRepository
from .item_media_repository import ItemMediaRepository
from .album_repository import AlbumRepository
from .tags_repository import TagsRepository
from .tag_implication_repository import TagImplicationRepository
from .tag_cooccurrence_repository import TagCooccurrenceRepository
from .tag_mutex_repository import TagMutexRepository
from .tag_feedback_repository import TagFeedbackRepository
from .ai_job_repository import AIJobRepository
from .ai_api_key_repository import AiApiKeyRepository

__all__ = [
    "Repository",
    "UserRepository",
    "SessionRepository",
    "FolderRepository",
    "PermissionRepository",

    "SafeRepository",
    "WebAuthnRepository",
    "ItemRepository",
    "ItemMediaRepository",
    "AlbumRepository",
    "TagsRepository",
    "TagImplicationRepository",
    "TagCooccurrenceRepository",
    "TagMutexRepository",
    "TagFeedbackRepository",
    "AIJobRepository",
    "AiApiKeyRepository",
]
