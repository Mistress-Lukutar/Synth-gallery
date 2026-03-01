"""Item repository - polymorphic base for all content types.

This repository handles the base 'items' table which provides
polymorphic storage for photos, videos, notes, files, etc.
"""
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from .base import Repository


class ItemRepository(Repository):
    """Repository for polymorphic items.
    
    Items can be:
    - media: photos and videos (details in item_media table)
    - note: text notes (details in item_notes table - future)
    - file: generic files (details in item_files table - future)
    """
    
    def create(
        self,
        item_type: str,
        folder_id: str,
        user_id: int,
        item_id: str = None,
        title: str = None,
        metadata: dict = None,
        safe_id: str = None,
        is_encrypted: bool = False,
        created_at: datetime = None
    ) -> str:
        """Create a new item.
        
        Args:
            item_type: 'media', 'note', 'file'
            folder_id: Parent folder ID
            user_id: Owner user ID
            item_id: Optional UUID (generated if not provided)
            title: Item title/name
            metadata: Type-specific metadata dict (stored as JSON)
            safe_id: Safe ID if in encrypted vault
            is_encrypted: Whether item is encrypted
            created_at: Creation timestamp
            
        Returns:
            New item UUID
        """
        if item_id is None:
            item_id = str(uuid.uuid4())
        
        self._execute(
            """INSERT INTO items 
               (id, type, folder_id, safe_id, user_id, created_at, 
                title, metadata, is_encrypted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, item_type, folder_id, safe_id, user_id,
                created_at or datetime.now(),
                title,
                json.dumps(metadata) if metadata else None,
                1 if is_encrypted else 0
            )
        )
        self._commit()
        return item_id
    
    def get_by_id(self, item_id: str) -> Optional[Dict]:
        """Get item by ID."""
        cursor = self._execute(
            "SELECT * FROM items WHERE id = ?",
            (item_id,)
        )
        row = cursor.fetchone()
        if row:
            item = dict(row)
            if item.get('metadata'):
                item['metadata'] = json.loads(item['metadata'])
            return item
        return None
    
    def get_by_folder(
        self, 
        folder_id: str, 
        item_type: str = None,
        sort_by: str = "created",
        include_subfolders: bool = False
    ) -> List[Dict]:
        """Get items in folder.
        
        Args:
            folder_id: Folder ID
            item_type: Filter by type ('media', 'note', etc.) or None for all
            sort_by: 'created' or 'title'
            include_subfolders: Include items from subfolders
        """
        if include_subfolders:
            folder_filter = """folder_id IN (
                WITH RECURSIVE subfolder_tree AS (
                    SELECT id FROM folders WHERE id = ?
                    UNION ALL
                    SELECT f.id FROM folders f 
                    JOIN subfolder_tree st ON f.parent_id = st.id
                )
                SELECT id FROM subfolder_tree
            )"""
            params = [folder_id]
        else:
            folder_filter = "folder_id = ?"
            params = [folder_id]
        
        # Type filter
        if item_type:
            type_filter = "AND type = ?"
            params.append(item_type)
        else:
            type_filter = ""
        
        # Sort order
        if sort_by == "title":
            order_by = "COALESCE(title, id) ASC"
        else:
            order_by = "created_at DESC"
        
        cursor = self._execute(
            f"""SELECT * FROM items 
                WHERE {folder_filter} {type_filter}
                ORDER BY {order_by}""",
            tuple(params)
        )
        items = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get('metadata'):
                item['metadata'] = json.loads(item['metadata'])
            items.append(item)
        return items
    
    def get_by_safe(self, safe_id: str, item_type: str = None) -> List[Dict]:
        """Get items in a safe."""
        if item_type:
            cursor = self._execute(
                """SELECT * FROM items 
                   WHERE safe_id = ? AND type = ?
                   ORDER BY created_at DESC""",
                (safe_id, item_type)
            )
        else:
            cursor = self._execute(
                """SELECT * FROM items 
                   WHERE safe_id = ?
                   ORDER BY created_at DESC""",
                (safe_id,)
            )
        items = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get('metadata'):
                item['metadata'] = json.loads(item['metadata'])
            items.append(item)
        return items
    
    def update(self, item_id: str, **kwargs) -> bool:
        """Update item fields.
        
        Args:
            item_id: Item ID
            **kwargs: Fields to update (title, metadata, folder_id, etc.)
        """
        allowed_fields = {'title', 'metadata', 'folder_id', 'safe_id'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        # Convert metadata dict to JSON
        if 'metadata' in updates and updates['metadata'] is not None:
            updates['metadata'] = json.dumps(updates['metadata'])
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [item_id]
        
        cursor = self._execute(
            f"UPDATE items SET {set_clause} WHERE id = ?",
            tuple(values)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def delete(self, item_id: str) -> bool:
        """Delete item and all its type-specific data (via CASCADE)."""
        cursor = self._execute(
            "DELETE FROM items WHERE id = ?",
            (item_id,)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def move_to_folder(self, item_id: str, folder_id: str) -> bool:
        """Move item to different folder."""
        cursor = self._execute(
            "UPDATE items SET folder_id = ? WHERE id = ?",
            (folder_id, item_id)
        )
        self._commit()
        return cursor.rowcount > 0
    
    def count_by_folder(self, folder_id: str, item_type: str = None) -> int:
        """Count items in folder."""
        if item_type:
            cursor = self._execute(
                "SELECT COUNT(*) as count FROM items WHERE folder_id = ? AND type = ?",
                (folder_id, item_type)
            )
        else:
            cursor = self._execute(
                "SELECT COUNT(*) as count FROM items WHERE folder_id = ?",
                (folder_id,)
            )
        row = cursor.fetchone()
        return row["count"] if row else 0
