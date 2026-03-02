"""Upload service - handles file uploads, thumbnails, and encryption.

This service encapsulates the business logic for uploading photos and videos,
including thumbnail generation, encryption, and database storage.
"""
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

from fastapi import UploadFile, HTTPException

from ...config import UPLOADS_DIR, THUMBNAILS_DIR, ALLOWED_MEDIA_TYPES
from ...infrastructure.repositories import PhotoRepository, ItemRepository, ItemMediaRepository, AlbumRepository
from ...infrastructure.services.encryption import EncryptionService
from ...infrastructure.services.media import (
    create_thumbnail_bytes, create_video_thumbnail_bytes, get_media_type
)
from ...infrastructure.services.metadata import extract_taken_date
from ...infrastructure.storage import get_storage, StorageInterface


class UploadService:
    """Service for handling file uploads.
    
    Responsibilities:
    - File validation (type, size)
    - Thumbnail generation
    - Encryption (server-side and client-side)
    - Database record creation
    - Safe/encrypted folder handling
    """
    
    # Allowed file extensions for client-side encrypted uploads
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.webm'}
    
    def __init__(
        self,
        photo_repository: PhotoRepository = None,
        uploads_dir: Path = UPLOADS_DIR,
        thumbnails_dir: Path = THUMBNAILS_DIR,
        storage: Optional[StorageInterface] = None,
        item_repository: ItemRepository = None,
        item_media_repository: ItemMediaRepository = None
    ):
        self.photo_repo = photo_repository
        self.uploads_dir = uploads_dir
        self.thumbnails_dir = thumbnails_dir
        self.storage = storage or get_storage()
        self.item_repo = item_repository
        self.media_repo = item_media_repository
    
    async def upload_single(
        self,
        file: UploadFile,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
        is_safe: bool = False,
        client_thumbnail: Optional[UploadFile] = None,
        thumb_dimensions: Tuple[int, int] = (0, 0)
    ) -> dict:
        """Upload a single photo or video.
        
        Args:
            file: The uploaded file
            folder_id: Target folder ID
            user_id: Uploading user ID
            user_dek: User's Data Encryption Key (for server-side encryption)
            is_safe: Whether uploading to an encrypted safe
            client_thumbnail: Client-provided thumbnail (for safe uploads)
            thumb_dimensions: Thumbnail width and height
            
        Returns:
            Dict with upload result: {id, filename, media_type, taken_at}
            
        Raises:
            HTTPException: On validation or processing errors
        """
        # Validate file
        self._validate_file(file, is_safe)
        
        # Determine media type
        media_type = self._get_media_type(file, is_safe)
        
        # Generate unique filename (extension-less storage)
        photo_id = str(uuid.uuid4())
        filename = photo_id  # No extension - stored in DB as content_type
        ext = Path(file.filename).suffix.lower() or (".mp4" if media_type == "video" else ".jpg")
        
        # Read file content
        file_content = await file.read()
        
        # Validate file content (protect against fake extensions)
        if not is_safe and not self._validate_file_content(file_content, media_type):
            raise HTTPException(
                status_code=400,
                detail=f"File content does not match {media_type} format"
            )
        
        # Process based on upload type
        if is_safe:
            # Client-side encrypted upload - save as-is
            taken_at, thumb_w, thumb_h = await self._process_safe_upload(
                photo_id, file_content, ext, client_thumbnail, thumb_dimensions
            )
            is_encrypted = True
        else:
            # Server-side processing
            taken_at, thumb_w, thumb_h = await self._process_regular_upload(
                photo_id, file_content, ext, media_type, user_dek
            )
            is_encrypted = user_dek is not None
        
        # Create database record with pre-generated photo_id and high-precision timestamp
        self.photo_repo.create(
            photo_id=photo_id,
            filename=filename,
            folder_id=folder_id,
            user_id=user_id,
            original_name=file.filename,
            media_type=media_type,
            taken_at=taken_at,
            is_encrypted=is_encrypted,
            thumb_width=thumb_w,
            thumb_height=thumb_h,
            content_type=file.content_type or ("video/mp4" if media_type == "video" else "image/jpeg"),
            uploaded_at=datetime.now()
        )
        
        return {
            "id": photo_id,
            "filename": filename,
            "media_type": media_type,
            "taken_at": taken_at.isoformat() if taken_at else None
        }
    
    def _validate_file(self, file: UploadFile, is_safe: bool = False) -> None:
        """Validate uploaded file.
        
        Raises:
            HTTPException: If file is invalid
        """
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="file is required")
        
        file_ext = Path(file.filename).suffix.lower()
        
        if is_safe:
            # For safe uploads, check extension only (encrypted content has octet-stream type)
            if file_ext not in self.ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400, 
                    detail="Images and videos only (jpg, png, gif, webp, mp4, webm)"
                )
        else:
            # Regular upload - check content type
            if file.content_type not in ALLOWED_MEDIA_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="Images and videos only (jpg, png, gif, webp, mp4, webm)"
                )
    
    def _validate_file_content(self, content: bytes, expected_media_type: str) -> bool:
        """Validate file content by checking magic bytes.
        
        Protects against uploading PHP scripts disguised as images.
        
        Args:
            content: File bytes
            expected_media_type: 'image' or 'video'
            
        Returns:
            True if content is valid for media type
        """
        if len(content) < 8:
            return False
        
        # Image magic bytes
        image_signatures = [
            (b'\xff\xd8', 'jpg'),           # JPEG
            (b'\x89PNG\r\n\x1a\n', 'png'),   # PNG
            (b'GIF87a', 'gif'),             # GIF
            (b'GIF89a', 'gif'),             # GIF
            (b'RIFF', 'webp'),              # WebP (starts with RIFF)
        ]
        
        # Video magic bytes
        video_signatures = [
            (b'\x00\x00\x00\x20ftyp', 'mp4'),  # MP4 (ftyp marker)
            (b'\x00\x00\x00\x18ftyp', 'mp4'),  # MP4 variant
            (b'\x1aE\xdf\xa3', 'webm'),       # WebM (matroska)
        ]
        
        if expected_media_type == 'image':
            for sig, _ in image_signatures:
                if content[:len(sig)] == sig:
                    return True
            # WebP check (RIFF....WEBP)
            if content[:4] == b'RIFF' and b'WEBP' in content[:20]:
                return True
        elif expected_media_type == 'video':
            for sig, _ in video_signatures:
                if content[:len(sig)] == sig:
                    return True
            # Check for mp4/mov ftyp marker
            if b'ftyp' in content[:20]:
                return True
        
        return False
    
    def _get_media_type(self, file: UploadFile, is_safe: bool = False) -> str:
        """Determine media type from file."""
        if is_safe:
            # Infer from extension for encrypted uploads
            ext = Path(file.filename).suffix.lower()
            return 'video' if ext in {'.mp4', '.webm'} else 'image'
        else:
            return get_media_type(file.content_type)
    
    async def _process_safe_upload(
        self,
        photo_id: str,
        file_content: bytes,
        ext: str,
        client_thumbnail: Optional[UploadFile],
        thumb_dimensions: Tuple[int, int]
    ) -> Tuple[datetime, int, int]:
        """Process client-side encrypted upload for safe.
        
        Args:
            photo_id: Photo UUID
            file_content: Encrypted file content
            ext: Original file extension (for EXIF extraction only)
            client_thumbnail: Optional client-provided thumbnail
            thumb_dimensions: Thumbnail width and height
            
        Returns:
            Tuple of (taken_at, thumb_width, thumb_height)
        """
        # Save encrypted file using storage backend (extension-less)
        await self.storage.upload(
            file_id=photo_id,
            content=file_content,
            folder="uploads"
        )
        
        # Save client-provided thumbnail if available (extension-less)
        thumb_w, thumb_h = thumb_dimensions
        if client_thumbnail:
            thumb_content = await client_thumbnail.read()
            await self.storage.upload(
                file_id=photo_id,
                content=thumb_content,
                folder="thumbnails"
            )
        
        return datetime.now(), thumb_w, thumb_h
    
    async def _process_regular_upload(
        self,
        photo_id: str,
        file_content: bytes,
        ext: str,
        media_type: str,
        user_dek: Optional[bytes]
    ) -> Tuple[datetime, int, int]:
        """Process regular (non-safe) upload with server-side processing.
        
        Args:
            photo_id: Photo UUID
            file_content: File bytes
            ext: Original file extension (for EXIF extraction only)
            media_type: 'image' or 'video'
            user_dek: Optional DEK for server-side encryption
            
        Returns:
            Tuple of (taken_at, thumb_width, thumb_height)
        """
        # Extract metadata before any processing
        taken_at = await self._extract_taken_date(file_content, ext)
        
        # Generate thumbnail
        thumb_bytes, thumb_w, thumb_h = await self._generate_thumbnail(
            file_content, media_type
        )
        
        # Save files (encrypted if DEK available) - extension-less storage
        if user_dek:
            # Encrypt with user's DEK
            encrypted_content = EncryptionService.encrypt_file(file_content, user_dek)
            encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, user_dek)
            
            await self.storage.upload(
                file_id=photo_id,
                content=encrypted_content,
                folder="uploads"
            )
            await self.storage.upload(
                file_id=photo_id,
                content=encrypted_thumb,
                folder="thumbnails"
            )
        else:
            # Save unencrypted
            await self.storage.upload(
                file_id=photo_id,
                content=file_content,
                folder="uploads"
            )
            await self.storage.upload(
                file_id=photo_id,
                content=thumb_bytes,
                folder="thumbnails"
            )
        
        return taken_at, thumb_w, thumb_h
    
    async def _extract_taken_date(self, file_content: bytes, ext: str) -> datetime:
        """Extract photo taken date from EXIF metadata."""
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = Path(tmp.name)
        
        try:
            taken_at = extract_taken_date(tmp_path)
            return taken_at or datetime.now()
        finally:
            tmp_path.unlink(missing_ok=True)
    
    async def _generate_thumbnail(
        self,
        file_content: bytes,
        media_type: str
    ) -> Tuple[bytes, int, int]:
        """Generate thumbnail from file content.
        
        Returns:
            Tuple of (thumbnail_bytes, width, height)
            
        Raises:
            HTTPException: If thumbnail generation fails
        """
        try:
            if media_type == "video":
                return create_video_thumbnail_bytes(file_content)
            else:
                return create_thumbnail_bytes(file_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Processing error: {e}")
    
    async def upload_album(
        self,
        files: list,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None,
        album_name: str = None
    ) -> dict:
        """Upload multiple files as an album.
        
        Args:
            files: List of uploaded files
            folder_id: Target folder ID
            user_id: Uploading user ID
            user_dek: User's Data Encryption Key
            
        Returns:
            Dict with album info and uploaded photos
        """
        if len(files) < 2:
            raise HTTPException(status_code=400, detail="Album requires at least 2 items")
        
        # Create album
        album_id = str(uuid.uuid4())
        self.photo_repo.create_album(album_id, folder_id, user_id, album_name)
        
        uploaded_photos = []
        for position, file in enumerate(files):
            try:
                result = await self.upload_single(
                    file=file,
                    folder_id=folder_id,
                    user_id=user_id,
                    user_dek=user_dek,
                    is_safe=False
                )
                result['position'] = position
                result['album_id'] = album_id
                uploaded_photos.append(result)
                
                # Link photo to album
                self.photo_repo.add_photo_to_album(result['id'], album_id, position)
            except HTTPException:
                # Skip invalid files
                continue
        
        return {
            "album_id": album_id,
            "photos": uploaded_photos,
            "photo_count": len(uploaded_photos)
        }
    
    async def upload_bulk(
        self,
        files: List[UploadFile],
        paths: List[str],
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes] = None
    ) -> dict:
        """Upload folder structure with files.
        
        Files at root level become individual photos.
        Files in subfolders become albums (one album per subfolder).
        
        Args:
            files: List of uploaded files
            paths: List of relative paths corresponding to each file
            folder_id: Target folder ID
            user_id: Uploading user ID
            user_dek: User's Data Encryption Key
            
        Returns:
            Dict with upload summary and created albums
        """
        if len(files) != len(paths):
            raise HTTPException(status_code=400, detail="Files and paths count mismatch")
        
        is_encrypted = user_dek is not None
        
        # Group files by their parent folder
        # Root level files go to '__root__'
        # Subfolder files go to their subfolder name
        groups = defaultdict(list)
        
        for file, path in zip(files, paths):
            parts = path.split('/')
            if len(parts) == 1:
                # Root level file
                groups['__root__'].append((file, path))
            elif len(parts) == 2:
                # First level subfolder
                album_name = parts[0]
                groups[album_name].append((file, path))
            # else: nested deeper - skip
        
        summary = {
            "total_files": len(files),
            "individual_photos": 0,
            "albums_created": 0,
            "photos_in_albums": 0,
            "failed": 0,
            "skipped_nested": len(files) - sum(len(g) for g in groups.values())
        }
        albums_created = []
        
        # Process root level files as individual photos
        for file, path in groups.pop('__root__', []):
            original_name = Path(file.filename).name
            result = await self._process_bulk_file(
                file, original_name, folder_id, user_id, user_dek, is_encrypted
            )
            if result:
                summary["individual_photos"] += 1
            else:
                summary["failed"] += 1
        
        # Process each subfolder as an album
        for album_name, album_files in groups.items():
            album_id = str(uuid.uuid4())
            self.photo_repo.create_album(album_id, album_name, folder_id, user_id)
            
            photos_uploaded = 0
            for position, (file, path) in enumerate(album_files):
                original_name = Path(file.filename).name
                result = await self._process_bulk_file(
                    file, original_name, folder_id, user_id, user_dek, is_encrypted,
                    album_id=album_id, position=position
                )
                if result:
                    photos_uploaded += 1
                    summary["photos_in_albums"] += 1
                else:
                    summary["failed"] += 1
            
            if photos_uploaded > 0:
                summary["albums_created"] += 1
                albums_created.append({
                    "id": album_id,
                    "name": album_name,
                    "photo_count": photos_uploaded
                })
            else:
                # No photos uploaded, delete empty album
                self.photo_repo.delete_album(album_id)
        
        return {
            "status": "ok",
            "summary": summary,
            "albums": albums_created
        }
    
    async def delete_photo(self, photo_id: str) -> bool:
        """Delete a single photo/item and its files.
        
        Args:
            photo_id: Photo/Item UUID to delete
            
        Returns:
            True if deleted, False if not found
        """
        # Phase 5: Try items table first, fallback to photos
        if self.item_repo and self.media_repo:
            item = self.item_repo.get_by_id(photo_id)
            if item:
                media = self.media_repo.get_by_item_id(photo_id)
                if media:
                    await self.storage.delete(media["filename"], folder="uploads")
                await self.storage.delete(photo_id, folder="thumbnails")
                self.item_repo.delete(photo_id)
                return True
        
        # Legacy: photos table
        photo = self.photo_repo.get_by_id(photo_id)
        if not photo:
            return False
        
        # Delete files from storage (extension-less)
        await self.storage.delete(photo["filename"], folder="uploads")
        await self.storage.delete(photo_id, folder="thumbnails")
        
        # Delete from database
        self.photo_repo.delete(photo_id)
        return True
    
    async def delete_album(self, album_id: str) -> tuple[int, int]:
        """Delete an album and all its items.
        
        Args:
            album_id: Album UUID to delete
            
        Returns:
            Tuple of (deleted_items_count, deleted_album_count)
        """
        # Phase 5: Use AlbumRepository to get items and delete them
        if self.item_repo:
            album_repo = AlbumRepository(self.item_repo._conn)
            items = album_repo.get_items(album_id)
            
            deleted_items = 0
            for item in items:
                # Delete item files
                media = self.media_repo.get_by_item_id(item["id"]) if self.media_repo else None
                if media:
                    await self.storage.delete(media["filename"], folder="uploads")
                await self.storage.delete(item["id"], folder="thumbnails")
                deleted_items += 1
            
            # Delete album (cascades to album_items)
            album_repo.delete(album_id)
            
            # Delete items from items table
            for item in items:
                self.item_repo.delete(item["id"])
            
            return deleted_items, 1
        
        # Legacy: photos table
        photos = self.photo_repo.delete_album_with_photos(album_id)
        
        deleted_photos = 0
        for photo in photos:
            await self.storage.delete(photo["filename"], folder="uploads")
            await self.storage.delete(photo["id"], folder="thumbnails")
            deleted_photos += 1
        
        return deleted_photos, 1
    
    async def _process_bulk_file(
        self,
        file: UploadFile,
        original_name: str,
        folder_id: str,
        user_id: int,
        user_dek: Optional[bytes],
        is_encrypted: bool,
        album_id: str = None,
        position: int = 0
    ) -> Optional[str]:
        """Process a single file for bulk upload.
        
        Returns:
            Photo ID if successful, None otherwise
        """
        if file.content_type not in ALLOWED_MEDIA_TYPES:
            return None
        
        media_type = get_media_type(file.content_type)
        photo_id = str(uuid.uuid4())
        ext = Path(original_name).suffix.lower() or (".mp4" if media_type == "video" else ".jpg")
        filename = photo_id  # Extension-less storage
        
        # Read file content
        file_content = await file.read()
        
        # Extract metadata from temp file
        taken_at = datetime.now()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = Path(tmp.name)
        
        try:
            extracted_date = extract_taken_date(tmp_path)
            if extracted_date:
                taken_at = extracted_date
        finally:
            tmp_path.unlink(missing_ok=True)
        
        # Create thumbnail
        try:
            if media_type == "video":
                thumb_bytes, thumb_w, thumb_h = create_video_thumbnail_bytes(file_content)
            else:
                thumb_bytes, thumb_w, thumb_h = create_thumbnail_bytes(file_content)
        except Exception:
            return None
        
        # Save files to storage (encrypted if DEK available, extension-less)
        content_type = file.content_type or ("video/mp4" if media_type == "video" else "image/jpeg")
        if is_encrypted:
            encrypted_content = EncryptionService.encrypt_file(file_content, user_dek)
            encrypted_thumb = EncryptionService.encrypt_file(thumb_bytes, user_dek)
            await self.storage.upload(
                file_id=filename,
                content=encrypted_content,
                folder="uploads"
            )
            await self.storage.upload(
                file_id=photo_id,
                content=encrypted_thumb,
                folder="thumbnails"
            )
        else:
            await self.storage.upload(
                file_id=filename,
                content=file_content,
                folder="uploads"
            )
            await self.storage.upload(
                file_id=photo_id,
                content=thumb_bytes,
                folder="thumbnails"
            )
        
        # Save to database with high-precision timestamp
        self.photo_repo.create(
            photo_id=photo_id,
            filename=filename,
            folder_id=folder_id,
            user_id=user_id,
            original_name=original_name,
            media_type=media_type,
            album_id=album_id,
            position=position,
            taken_at=taken_at,
            is_encrypted=is_encrypted,
            thumb_width=thumb_w,
            thumb_height=thumb_h,
            content_type=content_type,
            uploaded_at=datetime.now()
        )
        
        return photo_id
