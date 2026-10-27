'''
File:   __init__.py
Brief:  Storage package init - exports all storage classes and factory.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

Supports multiple backends: local filesystem, S3, MinIO, etc.
'''
from .base import StorageInterface, StorageError, FileNotFoundError, StorageConfig
from .local_storage import LocalStorage
from .s3_storage import S3Storage

from .factory import get_storage, get_storage_from_config

__all__ = [
    "StorageInterface",
    "StorageError",
    "FileNotFoundError",
    "StorageConfig",
    "LocalStorage",
    "S3Storage",

    "get_storage",
    "get_storage_from_config",
]
