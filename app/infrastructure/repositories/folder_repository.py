'''
File:   folder_repository.py
Brief:  Folder repository - handles all folder-related database operations.
Author: Mistress-Lukutar
Date:   2026-07-24
Version: v1.1.2
'''

from __future__ import annotations

import uuid
from typing import Optional

from app.infrastructure.repositories.base import Repository


class FolderRepository(Repository):
    '''Repository for folder entity operations.

    Folders form a tree structure where each folder can have:
    - One parent (optional, None for root folders)
    - Multiple children (subfolders)
    - Multiple photos and albums
    '''

    def create(
        self,
        name: str,
        user_id: int,
        parent_id: Optional[str] = None,
    ) -> str:
        '''Create a new folder.

        Args:
            name: Folder name
            user_id: Owner user ID
            parent_id: Parent folder ID (None for root)

        Returns:
            New folder UUID
        '''
        folder_id = str(uuid.uuid4())
        self._execute(
            '''INSERT INTO folders (id, name, parent_id, user_id)
               VALUES (?, ?, ?, ?)''',
            (folder_id, name.strip(), parent_id, user_id),
        )
        self._commit()
        return folder_id

    def get_by_id(self, folder_id: str) -> dict | None:
        '''Get folder by ID.

        Args:
            folder_id: Folder UUID

        Returns:
            Folder dict or None
        '''
        cursor = self._execute(
            'SELECT * FROM folders WHERE id = ?',
            (folder_id,),
        )
        return self._row_to_dict(cursor.fetchone())

    def update(self, folder_id: str, name: Optional[str] = None) -> bool:
        '''Update folder name.

        Args:
            folder_id: Folder ID
            name: New name (if provided)

        Returns:
            True if folder existed and was updated
        '''
        if name is None:
            return False

        cursor = self._execute(
            'UPDATE folders SET name = ? WHERE id = ?',
            (name.strip(), folder_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def delete(self, folder_id: str) -> list[str]:
        '''Delete folder and all its contents recursively.

        This deletes:
        - The folder and all subfolders (recursive)
        - All items in those folders (via CASCADE)
        - All albums in those folders

        Args:
            folder_id: Root folder ID to delete

        Returns:
            List of item IDs (used as filenames) that should be deleted from storage
        '''
        folder_ids = self._get_subtree_ids(folder_id)

        if not folder_ids:
            return []

        placeholders = ','.join('?' * len(folder_ids))
        cursor = self._execute(
            f'SELECT id FROM items WHERE folder_id IN ({placeholders})',
            tuple(folder_ids),
        )
        item_ids = [row['id'] for row in cursor.fetchall()]

        self._execute(
            f'DELETE FROM albums WHERE folder_id IN ({placeholders})',
            tuple(folder_ids),
        )
        self._execute(
            f'DELETE FROM items WHERE folder_id IN ({placeholders})',
            tuple(folder_ids),
        )
        self._execute(
            f'DELETE FROM folders WHERE id IN ({placeholders})',
            tuple(folder_ids),
        )

        self._commit()
        return item_ids

    def list_by_user(self, user_id: int) -> list[dict]:
        '''Get all folders owned by user.

        Args:
            user_id: User ID

        Returns:
            List of folder dicts
        '''
        cursor = self._execute(
            'SELECT * FROM folders WHERE user_id = ? ORDER BY name',
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_children(self, folder_id: str) -> list[dict]:
        '''Get direct child folders.

        Args:
            folder_id: Parent folder ID

        Returns:
            List of child folder dicts
        '''
        cursor = self._execute(
            'SELECT * FROM folders WHERE parent_id = ? ORDER BY name',
            (folder_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_tree(self, user_id: int, include_shared: bool = True) -> list[dict]:
        '''Get folder tree for sidebar with metadata.

        Args:
            user_id: User ID
            include_shared: Include folders shared with user

        Returns:
            List of folder dicts with metadata
        '''
        if include_shared:
            cursor = self._execute(
                '''SELECT f.*, u.display_name as owner_name,
                       (SELECT COUNT(*) FROM items i WHERE i.folder_id = f.id) as item_count
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ?
                      OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
                   ORDER BY f.name''',
                (user_id, user_id),
            )
        else:
            cursor = self._execute(
                '''SELECT f.*, u.display_name as owner_name,
                       (SELECT COUNT(*) FROM items i WHERE i.folder_id = f.id) as item_count
                   FROM folders f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.user_id = ?
                   ORDER BY f.name''',
                (user_id,),
            )

        return [dict(row) for row in cursor.fetchall()]

    def get_breadcrumbs(self, folder_id: str) -> list[dict]:
        '''Get breadcrumb path from root to folder.

        Args:
            folder_id: Target folder ID

        Returns:
            List of {id, name} dicts from root to target
        '''
        breadcrumbs: list[dict] = []
        current_id: Optional[str] = folder_id

        while current_id:
            cursor = self._execute(
                'SELECT id, name, parent_id FROM folders WHERE id = ?',
                (current_id,),
            )
            folder = cursor.fetchone()

            if folder:
                breadcrumbs.insert(0, {'id': folder['id'], 'name': folder['name']})
                current_id = folder['parent_id']
            else:
                break

        return breadcrumbs

    def move_to_folder(self, folder_id: str, new_parent_id: Optional[str]) -> bool:
        '''Move folder to new parent (or make root).

        Args:
            folder_id: Folder to move
            new_parent_id: New parent (None for root)

        Returns:
            True if successful
        '''
        cursor = self._execute(
            'UPDATE folders SET parent_id = ? WHERE id = ?',
            (new_parent_id, folder_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def exists(self, folder_id: str) -> bool:
        '''Check if folder exists.

        Args:
            folder_id: Folder ID

        Returns:
            True if exists
        '''
        cursor = self._execute(
            'SELECT 1 FROM folders WHERE id = ?',
            (folder_id,),
        )
        return cursor.fetchone() is not None

    def _get_subtree_ids(self, folder_id: str) -> list[str]:
        '''Get all folder IDs in subtree using recursive CTE.'''
        cursor = self._execute(
            '''WITH RECURSIVE folder_tree AS (
                SELECT id FROM folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
            )
            SELECT id FROM folder_tree''',
            (folder_id,),
        )
        return [row['id'] for row in cursor.fetchall()]

    # =========================================================================
    # Folder Encryption Keys (envelope encryption, not safes)
    # =========================================================================

    def get_folder_key(self, folder_id: str) -> dict | None:
        '''Get folder encryption key data.

        Args:
            folder_id: Folder ID

        Returns:
            Dict with encrypted_folder_dek, created_by or None
        '''
        cursor = self._execute(
            '''SELECT folder_id, encrypted_folder_dek, created_by, created_at
               FROM folder_keys WHERE folder_id = ?''',
            (folder_id,),
        )
        row = cursor.fetchone()
        if row:
            return {
                'folder_id': row['folder_id'],
                'encrypted_folder_dek': row['encrypted_folder_dek'],
                'created_by': row['created_by'],
                'created_at': row['created_at'],
            }
        return None

    def create_folder_key(
        self,
        folder_id: str,
        created_by: int,
        encrypted_folder_dek_b64: str,
    ) -> bool:
        '''Create folder encryption key.

        Args:
            folder_id: Folder ID
            created_by: User ID creating the key
            encrypted_folder_dek_b64: Base64-encoded JSON with encrypted DEK per user

        Returns:
            True if successful
        '''
        try:
            import base64
            encrypted_folder_dek = base64.b64decode(encrypted_folder_dek_b64).decode('utf-8')

            self._execute(
                '''INSERT INTO folder_keys (folder_id, encrypted_folder_dek, created_by)
                   VALUES (?, ?, ?)''',
                (folder_id, encrypted_folder_dek, created_by),
            )
            self._commit()
            return True
        except Exception:
            return False

    def update_folder_key(self, folder_id: str, encrypted_folder_dek_json: str) -> bool:
        '''Update folder encryption key.

        Args:
            folder_id: Folder ID
            encrypted_folder_dek_json: JSON string with encrypted DEK map

        Returns:
            True if successful
        '''
        try:
            self._execute(
                '''UPDATE folder_keys
                   SET encrypted_folder_dek = ?
                   WHERE folder_id = ?''',
                (encrypted_folder_dek_json, folder_id),
            )
            self._commit()
            return True
        except Exception:
            return False

    def delete_folder_key(self, folder_id: str) -> bool:
        '''Delete folder encryption key.

        Args:
            folder_id: Folder ID

        Returns:
            True if successful
        '''
        try:
            self._execute(
                'DELETE FROM folder_keys WHERE folder_id = ?',
                (folder_id,),
            )
            self._commit()
            return True
        except Exception:
            return False

    # =========================================================================
    # Folder Contents Operations
    # =========================================================================

    def get_subfolders(self, folder_id: str, user_id: int) -> list[dict]:
        '''Get subfolders accessible by user with photo counts.

        Args:
            folder_id: Parent folder ID
            user_id: User ID

        Returns:
            List of subfolder dicts with item_count
        '''
        cursor = self._execute('''
            SELECT f.*,
                   (
                       SELECT COUNT(*) FROM items i
                       WHERE i.folder_id IN (
                           WITH RECURSIVE subfolder_tree AS (
                               SELECT id FROM folders WHERE id = f.id
                               UNION ALL
                               SELECT child.id FROM folders child
                               JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                           )
                           SELECT id FROM subfolder_tree
                       )
                   ) as item_count
            FROM folders f
            WHERE f.parent_id = ? AND (
                f.user_id = ?
                OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
            )
            ORDER BY f.name
        ''', (folder_id, user_id, user_id))
        return [dict(row) for row in cursor.fetchall()]

    def get_albums_in_folder(self, folder_id: str) -> list[dict]:
        '''Get albums in folder with item counts.

        Args:
            folder_id: Folder ID

        Returns:
            List of album dicts with item_count, cover_item_id, cover thumbnail
            dimensions, and max item dates for sorting
        '''
        cursor = self._execute('''
            SELECT a.id, a.name, a.created_at as uploaded_at, a.folder_id, a.user_id,
                   (SELECT COUNT(*) FROM album_items WHERE album_id = a.id) as item_count,
                   COALESCE(a.cover_item_id,
                       (SELECT item_id FROM album_items WHERE album_id = a.id ORDER BY position LIMIT 1)
                   ) as cover_item_id,
                   cover_im.thumb_width as cover_thumb_width,
                   cover_im.thumb_height as cover_thumb_height,
                   COALESCE((SELECT MAX(added_at) FROM album_items WHERE album_id = a.id), a.created_at) as max_uploaded_at,
                   COALESCE((SELECT MAX(im.taken_at) FROM album_items ai
                            JOIN item_media im ON ai.item_id = im.item_id WHERE ai.album_id = a.id),
                            a.created_at) as max_taken_at
            FROM albums a
            LEFT JOIN item_media cover_im ON cover_im.item_id = COALESCE(a.cover_item_id,
                (SELECT item_id FROM album_items WHERE album_id = a.id ORDER BY position LIMIT 1)
            )
            WHERE a.folder_id = ?
            ORDER BY a.created_at DESC
        ''', (folder_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_standalone_items(self, folder_id: str) -> list[dict]:
        '''Get standalone items (not in any album) in folder.

        Args:
            folder_id: Folder ID

        Returns:
            List of item dicts with media data
        '''
        cursor = self._execute('''
            SELECT i.*, im.media_type, im.original_name, im.content_type,
                   im.width, im.height, im.thumb_width, im.thumb_height, im.taken_at
            FROM items i
            LEFT JOIN item_media im ON i.id = im.item_id
            WHERE i.folder_id = ?
              AND i.type = 'media'
              AND i.id NOT IN (
                  SELECT ai.item_id FROM album_items ai
                  WHERE ai.album_id IN (
                      SELECT id FROM albums WHERE folder_id = ?
                  )
              )
            ORDER BY i.uploaded_at DESC
        ''', (folder_id, folder_id))
        return [dict(row) for row in cursor.fetchall()]

    def get_item_count(self, folder_id: str) -> int:
        '''Get total item count in folder.

        Args:
            folder_id: Folder ID

        Returns:
            Total item count
        '''
        cursor = self._execute(
            'SELECT COUNT(*) as count FROM items WHERE folder_id = ?',
            (folder_id,),
        )
        row = cursor.fetchone()
        return row['count'] if row else 0

    def list_with_metadata(self, user_id: int) -> list[dict]:
        '''Get all folders accessible by user with metadata.

        Args:
            user_id: User ID

        Returns:
            List of folder dicts with metadata
        '''
        query = '''
            SELECT f.*, u.display_name as owner_name,
                   (
                       SELECT COUNT(*) FROM items i
                       WHERE i.folder_id IN (
                           WITH RECURSIVE subfolder_tree AS (
                               SELECT id FROM folders WHERE id = f.id
                               UNION ALL
                               SELECT child.id FROM folders child
                               JOIN subfolder_tree ON child.parent_id = subfolder_tree.id
                           )
                           SELECT id FROM subfolder_tree
                       )
                   ) as item_count,
                   CASE
                       WHEN f.user_id = ? THEN 'owner'
                       ELSE (SELECT permission FROM folder_permissions WHERE folder_id = f.id AND user_id = ?)
                   END as permission,
                   CASE
                       WHEN f.user_id != ? THEN NULL
                       WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'editor') THEN 'has_editors'
                       WHEN EXISTS(SELECT 1 FROM folder_permissions WHERE folder_id = f.id AND permission = 'viewer') THEN 'has_viewers'
                       ELSE 'private'
                   END as share_status
            FROM folders f
            JOIN users u ON f.user_id = u.id
            WHERE f.user_id = ?
               OR f.id IN (SELECT folder_id FROM folder_permissions WHERE user_id = ?)
            ORDER BY f.name
        '''

        params = (user_id, user_id, user_id, user_id, user_id)
        cursor = self._execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
