'''
File:   uploads.py
Brief:  Upload routes - unified upload handling for all media types.
Author: Mistress-Lukutar
Date:   2026-07-24
Version: v1.1.2
'''
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException

from app.application.services import FolderService, ItemService
from app.application.services.item_types import ItemType
from app.database import create_connection
from app.dependencies import require_user
from app.infrastructure.repositories import (
    AlbumRepository,
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
)
from app.infrastructure.services.encryption import dek_cache
from app.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


def get_item_service(db) -> ItemService:
    '''Get configured ItemService.'''
    return ItemService(
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db),
    )


async def _process_upload(
    file: UploadFile,
    folder_id: str,
    user: dict,
) -> dict:
    '''Process a single file upload of any registered item type.

    Delegates to :meth:`ItemService.process_upload`, which routes to the
    media or note pipeline based on the content type. Memory usage is
    bounded by the streaming pipeline; arbitrarily large media files
    (multi-GiB MKVs) are supported.
    '''
    db = create_connection()
    try:
        item_service = get_item_service(db)
        user_dek = dek_cache.get(user['id'])
        return await item_service.process_upload(
            file=file,
            folder_id=folder_id,
            user_id=user['id'],
            user_dek=user_dek,
        )
    finally:
        db.close()


@router.post('/api/uploads')
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str = Form(...),
):
    '''Upload a single file.

    Creates an Item record with type='media' and the associated ItemMedia
    row.
    '''
    user = require_user(request)

    # Permission check.
    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user['id']):
            raise HTTPException(403, 'Cannot upload to this folder')
    finally:
        db.close()

    item = await _process_upload(file=file, folder_id=folder_id, user=user)

    response = {
        'id': item['id'],
        'type': item.get('type', ItemType.MEDIA.value),
        'folder_id': folder_id,
        'title': item.get('title', ''),
        'filename': item['id'],  # Extension-less: filename = item_id
        'content_type': item.get('content_type'),
        'status': 'ok',
    }
    if response['type'] == ItemType.NOTE.value:
        response.update({
            'encoding': item.get('encoding'),
            'char_count': item.get('char_count'),
            'line_count': item.get('line_count'),
        })
    else:
        response.update({
            'media_type': item.get('media_type', 'image'),
            'thumb_width': item.get('thumb_width', 0),
            'thumb_height': item.get('thumb_height', 0),
            'taken_at': item.get('taken_at'),
        })
    return response


@router.post('/api/uploads/batch')
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
):
    '''Upload multiple files. Creates one Item per file.'''
    user = require_user(request)

    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user['id']):
            raise HTTPException(403, 'Cannot upload to this folder')
    finally:
        db.close()

    results = []
    errors = []

    for file in files:
        try:
            item = await _process_upload(
                file=file, folder_id=folder_id, user=user
            )
            results.append({
                'id': item['id'],
                'type': ItemType.MEDIA.value,
                'folder_id': folder_id,
                'media_type': item.get('media_type', 'image'),
                'title': item.get('title', ''),
            })
        except Exception as exc:
            errors.append({'filename': file.filename, 'error': str(exc)})

    return {
        'status': 'ok' if not errors else 'partial',
        'items': results,
        'errors': errors,
        'total': len(files),
        'successful': len(results),
        'failed': len(errors),
    }


@router.post('/upload-bulk')
async def upload_bulk(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    paths: str = Form(...),
):
    '''Bulk upload a folder structure with files.

    Creates subfolders and albums from the directory structure. Files in the
    root go to the target folder; files in subfolders create albums.
    '''
    user = require_user(request)

    try:
        file_paths = json.loads(paths)
    except json.JSONDecodeError:
        raise HTTPException(400, 'Invalid paths JSON')

    if len(files) != len(file_paths):
        raise HTTPException(400, 'Files and paths count mismatch')

    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user['id']):
            raise HTTPException(403, 'Cannot upload to this folder')
    finally:
        db.close()

    # Group files by their parent directory.
    root_files = []
    album_groups: dict[str, list[tuple[UploadFile, str]]] = {}
    skipped_nested = 0

    for file, relative_path in zip(files, file_paths):
        relative_path = relative_path.replace('\\', '/')
        parts = relative_path.split('/')

        if len(parts) == 1:
            root_files.append((file, parts[0]))
        elif len(parts) == 2:
            album_groups.setdefault(parts[0], []).append((file, parts[1]))
        else:
            skipped_nested += 1

    individual_photos = 0
    albums_created = 0
    photos_in_albums = 0
    failed = 0
    errors: list[str] = []

    db = create_connection()
    try:
        folder_repo = FolderRepository(db)
        album_repo = AlbumRepository(db)
        folder_service = FolderService(folder_repo)

        # Root-level files go straight into the target folder.
        for file, filename in root_files:
            try:
                await _process_upload(
                    file=file, folder_id=folder_id, user=user
                )
                individual_photos += 1
            except Exception as exc:
                failed += 1
                errors.append(f'{filename}: {exc}')

        # Subfolders become albums.
        for album_name, album_files in album_groups.items():
            try:
                subfolder = folder_service.create_folder(
                    name=album_name,
                    user_id=user['id'],
                    parent_id=folder_id,
                )

                item_ids = []
                for file, _ in album_files:
                    try:
                        item = await _process_upload(
                            file=file,
                            folder_id=subfolder['id'],
                            user=user,
                        )
                        item_ids.append(item['id'])
                        photos_in_albums += 1
                    except Exception as exc:
                        failed += 1
                        errors.append(f'{file.filename}: {exc}')

                if item_ids:
                    album_id = album_repo.create(
                        folder_id=subfolder['id'],
                        user_id=user['id'],
                        name=album_name,
                    )
                    for position, item_id in enumerate(item_ids):
                        album_repo.add_item(album_id, item_id, position)
                    albums_created += 1
            except Exception as exc:
                failed += len(album_files)
                errors.append(f'Album {album_name}: {exc}')
    finally:
        db.close()

    return {
        'status': 'ok' if failed == 0 else 'partial',
        'summary': {
            'total_files': len(files),
            'individual_photos': individual_photos,
            'albums_created': albums_created,
            'photos_in_albums': photos_in_albums,
            'failed': failed,
            'skipped_nested': skipped_nested,
        },
        'errors': errors if errors else None,
    }


@router.post('/upload-album')
async def upload_album(
    request: Request,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(...),
    album_name: str = Form(''),
):
    '''Upload multiple files as an album.

    Creates items and an album containing them.
    '''
    user = require_user(request)

    if len(files) < 2:
        raise HTTPException(400, 'Album requires at least 2 files')

    from .deps import get_permission_service
    db = create_connection()
    try:
        perm_service = get_permission_service(db)
        if not perm_service.can_edit(folder_id, user['id']):
            raise HTTPException(403, 'Cannot upload to this folder')
    finally:
        db.close()

    item_ids = []
    for file in files:
        try:
            item = await _process_upload(
                file=file, folder_id=folder_id, user=user
            )
            item_ids.append(item['id'])
        except Exception:
            logger.exception('Failed to upload %s', file.filename)

    db = create_connection()
    try:
        album_repo = AlbumRepository(db)
        item_service = ItemService(
            item_repository=ItemRepository(db),
            item_media_repository=ItemMediaRepository(db),
        )

        if not album_name:
            album_name = f"Album {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        album_id = album_repo.create(
            folder_id=folder_id,
            user_id=user['id'],
            name=album_name,
        )

        for position, new_item_id in enumerate(item_ids):
            album_repo.add_item(album_id, new_item_id, position)

        uploaded_items = []
        for new_item_id in item_ids:
            item = item_service.get_item(new_item_id)
            if item:
                uploaded_items.append({
                    'id': item['id'],
                    'title': item.get('title', ''),
                    'media_type': item.get('media_type', 'image'),
                    'content_type': item.get('content_type'),
                    'thumb_width': item.get('thumb_width'),
                    'thumb_height': item.get('thumb_height'),
                    'taken_at': item.get('taken_at'),
                })

        return {
            'status': 'ok',
            'album_id': album_id,
            'item_count': len(item_ids),
            'items': uploaded_items,
        }
    finally:
        db.close()
