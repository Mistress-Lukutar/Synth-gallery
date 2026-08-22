"""Integration tests for the albums.name NOT NULL migration and validation.

Covers:
- Fresh databases create albums.name as NOT NULL and reject NULL inserts
- The idempotent init_db migration backfills legacy NULL-name albums with
  a deterministic 'Untitled (id8)' name, rebuilds the table with the
  constraint and preserves album_items + indexes
- The album API rejects empty names on create and rename
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.database import init_db


def _album_name_column(db) -> sqlite3.Row:
    cursor = db.execute('PRAGMA table_info(albums)')
    return next(row for row in cursor.fetchall() if row['name'] == 'name')


class TestSchemaConstraint:
    '''Fresh schemas enforce NOT NULL on albums.name.'''

    def test_albums_name_is_not_null(self, db_connection):
        col = _album_name_column(db_connection)
        assert col['notnull'] == 1

    def test_null_name_insert_rejected(self, db_connection, test_user, test_folder):
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                '''INSERT INTO albums (id, name, folder_id, user_id)
                   VALUES ('cccccccc-0000-0000-0000-000000000003',
                           NULL, ?, ?)''',
                (test_folder, test_user['id']),
            )


class TestMigration:
    '''Legacy nullable-name tables are migrated by revision 0003.'''

    def _simulate_legacy_table(self, db) -> str:
        '''Replace albums with a pre-migration nullable-name table.'''
        album_id = 'aaaaaaaa-bbbb-cccc-0000-000000000001'
        db.execute('PRAGMA foreign_keys = OFF')
        db.execute('DROP TABLE albums')
        db.execute("""
            CREATE TABLE albums (
                id TEXT PRIMARY KEY,
                name TEXT,
                folder_id TEXT,
                user_id INTEGER,
                cover_item_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (cover_item_id) REFERENCES items(id) ON DELETE SET NULL
            )
        """)
        db.execute(
            '''INSERT INTO albums (id, name, folder_id, user_id)
               VALUES (?, NULL, ?, ?)''',
            (album_id, None, None),
        )
        db.execute(
            'INSERT INTO album_items (album_id, item_id, position) '
            'VALUES (?, ?, 0)',
            (album_id, 'zzzzzzzz-0000-0000-0000-000000000009'),
        )
        db.commit()
        db.execute('PRAGMA foreign_keys = ON')
        return album_id

    def _stamp_before_revision(self, db) -> None:
        '''Pretend the database has not seen revision 0003 yet.'''
        db.execute('DELETE FROM alembic_version')
        db.execute("INSERT INTO alembic_version (version_num) VALUES ('0002')")
        db.commit()

    def test_migration_backfills_and_enforces_not_null(
        self, db_connection
    ):
        album_id = self._simulate_legacy_table(db_connection)
        self._stamp_before_revision(db_connection)

        init_db()  # applies the pending revision

        row = db_connection.execute(
            'SELECT name FROM albums WHERE id = ?', (album_id,)
        ).fetchone()
        assert row is not None
        assert row['name'] == f'Untitled ({album_id[:8]})'

        # Constraint now enforced at the schema level
        assert _album_name_column(db_connection)['notnull'] == 1

        # album_items survived the rebuild
        links = db_connection.execute(
            'SELECT item_id FROM album_items WHERE album_id = ?', (album_id,)
        ).fetchall()
        assert [r['item_id'] for r in links] == [
            'zzzzzzzz-0000-0000-0000-000000000009'
        ]

        # the folder index was recreated
        idx = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_albums_folder_id'"
        ).fetchone()
        assert idx is not None

    def test_migration_is_idempotent(self, db_connection):
        self._simulate_legacy_table(db_connection)
        self._stamp_before_revision(db_connection)
        init_db()
        names_before = [
            r['name'] for r in db_connection.execute(
                'SELECT name FROM albums ORDER BY id'
            ).fetchall()
        ]
        init_db()  # second run must be a no-op
        names_after = [
            r['name'] for r in db_connection.execute(
                'SELECT name FROM albums ORDER BY id'
            ).fetchall()
        ]
        assert names_before == names_after


class TestApiValidation:
    '''The album API rejects empty names.'''

    def test_create_album_with_empty_name_rejected(
        self, authenticated_client: TestClient, test_folder, csrf_token
    ):
        resp = authenticated_client.post(
            '/api/albums',
            json={'name': '', 'folder_id': test_folder, 'item_ids': []},
            headers={'X-CSRF-Token': csrf_token},
        )
        assert resp.status_code == 422

    def test_rename_album_to_empty_name_rejected(
        self, authenticated_client: TestClient, test_folder, csrf_token
    ):
        create = authenticated_client.post(
            '/api/albums',
            json={'name': 'Valid Name', 'folder_id': test_folder,
                  'item_ids': []},
            headers={'X-CSRF-Token': csrf_token},
        )
        assert create.status_code == 200
        album_id = create.json()['album_id']

        resp = authenticated_client.put(
            f'/api/albums/{album_id}',
            json={'name': ''},
            headers={'X-CSRF-Token': csrf_token},
        )
        assert resp.status_code == 422

    def test_rename_album_works(
        self, authenticated_client: TestClient, test_folder, csrf_token,
        db_connection
    ):
        create = authenticated_client.post(
            '/api/albums',
            json={'name': 'Old Name', 'folder_id': test_folder,
                  'item_ids': []},
            headers={'X-CSRF-Token': csrf_token},
        )
        album_id = create.json()['album_id']

        resp = authenticated_client.put(
            f'/api/albums/{album_id}',
            json={'name': 'New Name', 'folder_id': test_folder},
            headers={'X-CSRF-Token': csrf_token},
        )
        assert resp.status_code == 200

        row = db_connection.execute(
            'SELECT name FROM albums WHERE id = ?', (album_id,)
        ).fetchone()
        assert row['name'] == 'New Name'
