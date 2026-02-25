"""S3-compatible storage implementation (AWS S3, MinIO, DigitalOcean Spaces)."""
from pathlib import Path
from typing import BinaryIO, Optional, Union, Iterator
import io

from .base import (
    StorageInterface,
    StorageConfig,
    StorageError,
    FileNotFoundError as StorageFileNotFoundError,
    UploadError,
    DownloadError,
    DeleteError
)

# Optional dependency - only needed for S3 storage
try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    boto3 = None
    ClientError = Exception


class S3Storage(StorageInterface):
    """S3-compatible storage backend.
    
    Supports:
    - AWS S3
    - MinIO
    - DigitalOcean Spaces
    - Any S3-compatible API
    """
    
    def __init__(self, config: StorageConfig):
        """Initialize S3 storage.
        
        Args:
            config: Storage configuration with S3 settings
        """
        if not HAS_BOTO3:
            raise ImportError(
                "boto3 is required for S3 storage. "
                "Install with: pip install boto3"
            )
        
        if config.backend not in ("s3", "minio"):
            raise ValueError(
                f"S3Storage requires backend='s3' or 'minio', got '{config.backend}'"
            )
        
        self.config = config
        self.bucket = config.bucket_name
        
        # Build boto3 client kwargs
        client_kwargs = {
            "service_name": "s3",
            "aws_access_key_id": config.access_key,
            "aws_secret_access_key": config.secret_key,
            "region_name": config.region,
        }
        
        # Custom endpoint for MinIO/DigitalOcean
        if config.endpoint_url:
            client_kwargs["endpoint_url"] = config.endpoint_url
            client_kwargs["use_ssl"] = config.use_ssl
        
        self.client = boto3.client(**client_kwargs)
        
        # Ensure bucket exists
        self._ensure_bucket()
    
    def _ensure_bucket(self):
        """Create bucket if it doesn't exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == '404':
                # Bucket doesn't exist, create it
                try:
                    if self.config.region == 'us-east-1':
                        self.client.create_bucket(Bucket=self.bucket)
                    else:
                        self.client.create_bucket(
                            Bucket=self.bucket,
                            CreateBucketConfiguration={
                                'LocationConstraint': self.config.region
                            }
                        )
                except ClientError as create_error:
                    raise StorageError(
                        f"Failed to create bucket {self.bucket}: {create_error}"
                    )
            else:
                raise StorageError(
                    f"Cannot access bucket {self.bucket}: {e}"
                )
    
    def _get_key(self, file_id: str, folder: str) -> str:
        """Get S3 object key."""
        # Sanitize file_id
        safe_id = Path(file_id).name
        return f"{folder}/{safe_id}"
    
    async def upload(
        self,
        file_id: str,
        content: Union[bytes, BinaryIO],
        folder: str = "uploads",
        content_type: Optional[str] = None
    ) -> str:
        """Upload file to S3."""
        key = self._get_key(file_id, folder)
        
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type
        
        try:
            if isinstance(content, bytes):
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=content,
                    **extra_args
                )
            else:
                # Read from file-like object
                body = content.read() if hasattr(content, 'read') else content
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    **extra_args
                )
            
            return key
            
        except ClientError as e:
            raise UploadError(f"Failed to upload {file_id}: {e}")
    
    async def download(self, file_id: str, folder: str = "uploads") -> bytes:
        """Download file from S3."""
        key = self._get_key(file_id, folder)
        
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response['Body'].read()
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'NoSuchKey':
                raise StorageFileNotFoundError(f"File not found: {file_id}")
            raise DownloadError(f"Failed to download {file_id}: {e}")
    
    def get_stream(self, file_id: str, folder: str = "uploads") -> BinaryIO:
        """Get file as stream from S3."""
        key = self._get_key(file_id, folder)
        
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            # Return the StreamingBody as file-like object
            return response['Body']
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'NoSuchKey':
                raise StorageFileNotFoundError(f"File not found: {file_id}")
            raise DownloadError(f"Failed to stream {file_id}: {e}")
    
    async def delete(self, file_id: str, folder: str = "uploads") -> bool:
        """Delete file from S3."""
        key = self._get_key(file_id, folder)
        
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'NoSuchKey':
                return False
            raise DeleteError(f"Failed to delete {file_id}: {e}")
    
    def exists(self, file_id: str, folder: str = "uploads") -> bool:
        """Check if file exists in S3."""
        key = self._get_key(file_id, folder)
        
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code in ('404', 'NoSuchKey'):
                return False
            raise StorageError(f"Failed to check existence of {file_id}: {e}")
    
    def get_url(
        self,
        file_id: str,
        folder: str = "uploads",
        expires: Optional[int] = None
    ) -> str:
        """Get presigned URL for file."""
        key = self._get_key(file_id, folder)
        
        if expires is None:
            # Return direct URL (if bucket is public)
            endpoint = self.config.endpoint_url or f"https://s3.{self.config.region}.amazonaws.com"
            return f"{endpoint}/{self.bucket}/{key}"
        
        # Generate presigned URL
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': key},
                ExpiresIn=expires
            )
            return url
        except ClientError as e:
            raise StorageError(f"Failed to generate URL for {file_id}: {e}")
    
    def get_path(self, file_id: str, folder: str = "uploads") -> str:
        """Get S3 object key."""
        return self._get_key(file_id, folder)
    
    async def copy(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Copy file within S3."""
        source_key = self._get_key(source_id, source_folder)
        dest_key = self._get_key(dest_id, dest_folder)
        
        copy_source = {'Bucket': self.bucket, 'Key': source_key}
        
        try:
            self.client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket,
                Key=dest_key
            )
            return dest_key
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'NoSuchKey':
                raise StorageFileNotFoundError(f"Source file not found: {source_id}")
            raise StorageError(f"Failed to copy {source_id} to {dest_id}: {e}")
    
    async def move(
        self,
        source_id: str,
        dest_id: str,
        source_folder: str = "uploads",
        dest_folder: str = "uploads"
    ) -> str:
        """Move file within S3."""
        # S3 doesn't have move, so copy + delete
        dest_key = await self.copy(source_id, dest_id, source_folder, dest_folder)
        await self.delete(source_id, source_folder)
        return dest_key
    
    def list_files(self, folder: str = "uploads", prefix: Optional[str] = None) -> Iterator[str]:
        """List files in S3 bucket."""
        list_prefix = f"{folder}/"
        if prefix:
            list_prefix += prefix
        
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket, Prefix=list_prefix)
            
            for page in pages:
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    # Extract file_id from key (remove folder/ prefix)
                    if key.startswith(f"{folder}/"):
                        file_id = key[len(f"{folder}/"):]
                        if file_id:  # Skip empty strings
                            yield file_id
        except ClientError as e:
            raise StorageError(f"Failed to list files: {e}")
    
    async def get_size(self, file_id: str, folder: str = "uploads") -> int:
        """Get file size from S3."""
        key = self._get_key(file_id, folder)
        
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return response['ContentLength']
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code in ('404', 'NoSuchKey'):
                raise StorageFileNotFoundError(f"File not found: {file_id}")
            raise StorageError(f"Failed to get size of {file_id}: {e}")
