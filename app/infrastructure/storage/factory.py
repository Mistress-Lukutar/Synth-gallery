"""Factory for creating storage backends."""
import os
from pathlib import Path
from typing import Optional

from ...config import BASE_DIR, UPLOADS_DIR, THUMBNAILS_DIR

from .base import StorageConfig
from .local_storage import LocalStorage


# Singleton instance
_storage_instance: Optional[LocalStorage] = None


def get_storage_config() -> StorageConfig:
    """Get storage configuration from environment variables.
    
    Environment variables:
    - STORAGE_BACKEND: 'local' (default), 's3', 'minio'
    - STORAGE_BASE_PATH: Base path for local storage (default: project root)
    
    For S3:
    - S3_BUCKET: Bucket name
    - S3_ENDPOINT: Custom endpoint (for MinIO)
    - S3_ACCESS_KEY: Access key
    - S3_SECRET_KEY: Secret key
    - S3_REGION: Region (default: us-east-1)
    - S3_USE_SSL: Use SSL (default: true)
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    
    if backend == "local":
        base_path = os.environ.get("STORAGE_BASE_PATH")
        if base_path:
            base_path = Path(base_path)
        else:
            # Default: parent of uploads directory
            base_path = Path(UPLOADS_DIR).parent
        
        return StorageConfig(
            backend="local",
            base_path=base_path
        )
    
    elif backend in ("s3", "minio"):
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable is required for S3 storage")
        
        endpoint = os.environ.get("S3_ENDPOINT")
        access_key = os.environ.get("S3_ACCESS_KEY")
        secret_key = os.environ.get("S3_SECRET_KEY")
        region = os.environ.get("S3_REGION", "us-east-1")
        use_ssl = os.environ.get("S3_USE_SSL", "true").lower() == "true"
        
        return StorageConfig(
            backend=backend,
            bucket_name=bucket,
            endpoint_url=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            use_ssl=use_ssl
        )
    
    else:
        raise ValueError(f"Unknown storage backend: {backend}")


def get_storage_from_config(config: StorageConfig) -> LocalStorage:
    """Create storage backend from configuration.
    
    Args:
        config: Storage configuration
        
    Returns:
        Storage backend instance
    """
    if config.backend == "local":
        return LocalStorage(config)
    
    elif config.backend in ("s3", "minio"):
        from .s3_storage import S3Storage
        return S3Storage(config)
    
    else:
        raise ValueError(f"Unknown storage backend: {config.backend}")


def get_storage() -> LocalStorage:
    """Get or create singleton storage instance.
    
    This is the main entry point for getting storage.
    The instance is cached for reuse.
    
    Returns:
        Storage backend instance
    """
    global _storage_instance
    
    if _storage_instance is None:
        config = get_storage_config()
        _storage_instance = get_storage_from_config(config)
    
    return _storage_instance


def reset_storage():
    """Reset storage singleton (useful for testing)."""
    global _storage_instance
    _storage_instance = None


def create_storage_for_migration(
    source_backend: str,
    dest_backend: str,
    source_config: Optional[StorageConfig] = None,
    dest_config: Optional[StorageConfig] = None
) -> tuple[LocalStorage, LocalStorage]:
    """Create source and destination storage for migration.
    
    Args:
        source_backend: Source backend type
        dest_backend: Destination backend type
        source_config: Optional source configuration
        dest_config: Optional destination configuration
        
    Returns:
        Tuple of (source_storage, dest_storage)
    """
    if source_config is None:
        source_config = get_storage_config()
        source_config.backend = source_backend
    
    if dest_config is None:
        dest_config = get_storage_config()
        dest_config.backend = dest_backend
    
    source = get_storage_from_config(source_config)
    dest = get_storage_from_config(dest_config)
    
    return source, dest
