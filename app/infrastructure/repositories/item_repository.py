'''
File:   item_repository.py
Brief:  Item repository - polymorphic base for all content types.
Author: Mistress-Lukutar
Date:   2026-07-24
Version: v1.1.3
'''

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.infrastructure.repositories.base import Repository


class ItemRepository(Repository):
    '''Repository for polymorphic items.

    Items can be:
    - media: photos and videos (details in item_media table)
    - note: text notes (details in item_notes table - future)
    - file: generic files (details in item_files table - future)
    '''

    def create(
        self,
        item_type: str,
        folder_id: str,
        user_id: int,
        item_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        uploaded_at: Optional[datetime] = None,
    ) -> str:
        '''Create a new item.

        Args:
            item_type: 'media', 'note', 'file'
            folder_id: Parent folder ID
            user_id: Owner user ID
            item_id: Optional UUID (generated if not provided)
            title: Item title/name
            description: Item description
            uploaded_at: Upload timestamp

        Returns:
            New item UUID
        '''
        if item_id is None:
            item_id = str(uuid.uuid4())

        self._execute(
            '''INSERT INTO items
               (id, type, folder_id, user_id, uploaded_at,
                title, description)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                item_id,
                item_type,
                folder_id,
                user_id,
                uploaded_at or datetime.now(),
                title,
                description,
            ),
        )
        self._commit()
        return item_id

    def get_by_id(self, item_id: str) -> Optional[dict]:
        '''Get item by ID.'''
        cursor = self._execute(
            'SELECT * FROM items WHERE id = ?',
            (item_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def get_by_folder(
        self,
        folder_id: str,
        item_type: Optional[str] = None,
        sort_by: str = 'created',
        include_subfolders: bool = False,
    ) -> list[dict]:
        '''Get items in folder.

        Args:
            folder_id: Folder ID
            item_type: Filter by type ('media', 'note', etc.) or None for all
            sort_by: 'created' or 'title'
            include_subfolders: Include items from subfolders
        '''
        if include_subfolders:
            folder_filter = '''folder_id IN (
                WITH RECURSIVE subfolder_tree AS (
                    SELECT id FROM folders WHERE id = ?
                    UNION ALL
                    SELECT f.id FROM folders f
                    JOIN subfolder_tree st ON f.parent_id = st.id
                )
                SELECT id FROM subfolder_tree
            )'''
            params = [folder_id]
        else:
            folder_filter = 'folder_id = ?'
            params = [folder_id]

        if item_type:
            type_filter = 'AND type = ?'
            params.append(item_type)
        else:
            type_filter = ''

        if sort_by == 'title':
            order_by = 'COALESCE(title, id) ASC'
        elif sort_by == 'taken':
            order_by = 'uploaded_at DESC'
        else:
            order_by = 'uploaded_at DESC'

        cursor = self._execute(
            f'''SELECT * FROM items
                WHERE {folder_filter} {type_filter}
                ORDER BY {order_by}''',
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update(self, item_id: str, **kwargs) -> bool:
        '''Update item fields.

        Args:
            item_id: Item ID
            **kwargs: Fields to update (title, folder_id, description)
        '''
        allowed_fields = {'title', 'folder_id', 'description'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
        values = list(updates.values()) + [item_id]

        cursor = self._execute(
            f'UPDATE items SET {set_clause} WHERE id = ?',
            tuple(values),
        )
        self._commit()
        return cursor.rowcount > 0

    def delete(self, item_id: str) -> bool:
        '''Delete item and all its type-specific data (via CASCADE).'''
        cursor = self._execute(
            'DELETE FROM items WHERE id = ?',
            (item_id,),
        )
        self._commit()
        return cursor.rowcount > 0

    def move_to_folder(self, item_id: str, folder_id: str) -> bool:
        '''Move item to different folder.'''
        cursor = self._execute(
            'UPDATE items SET folder_id = ? WHERE id = ?',
            (folder_id, item_id),
        )
        self._commit()
        return cursor.rowcount > 0

    def count_by_folder(self, folder_id: str, item_type: Optional[str] = None) -> int:
        '''Count items in folder.'''
        if item_type:
            cursor = self._execute(
                'SELECT COUNT(*) as count FROM items WHERE folder_id = ? AND type = ?',
                (folder_id, item_type),
            )
        else:
            cursor = self._execute(
                'SELECT COUNT(*) as count FROM items WHERE folder_id = ?',
                (folder_id,),
            )
        row = cursor.fetchone()
        return row['count'] if row else 0

    def get_media_with_details(
        self,
        folder_id: str,
        media_type: Optional[str] = None,
        sort_by: str = 'uploaded',
    ) -> list[dict]:
        '''Get media items in folder with their ``item_media`` details.

        This is the single read-model for the ``items JOIN item_media`` shape;
        it consolidates the JOIN previously duplicated on
        ``ItemMediaRepository``. Returns base item fields plus media specifics.

        Args:
            folder_id: Folder ID
            media_type: 'image', 'video', or None for all media
            sort_by: 'uploaded', 'taken', or 'title'

        Returns:
            List of dicts with base item + media detail fields.
        '''
        if sort_by == 'taken':
            order_by = 'COALESCE(im.taken_at, i.uploaded_at) DESC'
        elif sort_by == 'title':
            order_by = 'COALESCE(im.original_name, i.title, i.id) ASC'
        else:
            order_by = 'i.uploaded_at DESC'

        media_filter = 'AND im.media_type = ?' if media_type else ''
        params = [folder_id]
        if media_type:
            params.append(media_type)

        cursor = self._execute(
            f'''SELECT
                i.*,
                im.media_type, im.original_name, im.content_type,
                im.width, im.height, im.duration,
                im.thumb_width, im.thumb_height, im.taken_at
               FROM items i
               JOIN item_media im ON i.id = im.item_id
               WHERE i.folder_id = ? AND i.type = 'media' {media_filter}
               ORDER BY {order_by}''',
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_metadata(
        self,
        item_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        '''Update item metadata and set updated_at timestamp.

        Args:
            item_id: Item ID
            title: New title (optional)
            description: New description (optional)

        Returns:
            True if updated
        '''
        updates = []
        values = []

        if title is not None:
            updates.append('title = ?')
            values.append(title)
        if description is not None:
            updates.append('description = ?')
            values.append(description)

        if not updates:
            return False

        updates.append('updated_at = CURRENT_TIMESTAMP')
        values.append(item_id)

        set_clause = ', '.join(updates)
        cursor = self._execute(
            f'UPDATE items SET {set_clause} WHERE id = ?',
            tuple(values),
        )
        self._commit()
        return cursor.rowcount > 0
