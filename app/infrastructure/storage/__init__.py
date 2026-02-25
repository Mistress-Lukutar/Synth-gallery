"""Storage abstraction layer for file operations.

Supports multiple backends: local filesystem, S3, MinIO, etc.
"""
from .base import StorageInterface, StorageError, FileNotFoundError, StorageConfig
from .local_storage import LocalStorage
from .factory import get_storage, get_storage_from_config

__all__ = [
    "StorageInterface",
    "StorageError",
    "FileNotFoundError",
    "StorageConfig",
    "LocalStorage",
    "get_storage",
    "get_storage_from_config",
]
