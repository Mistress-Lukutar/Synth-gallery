"""Integration tests for note content editing and note cover images.

Covers:
- PUT /api/items/{id}/content: roundtrip, validation, permissions
- PUT /api/items/{id}/cover: set/clear, target validation, permissions
- Note thumbnail proxy (serves the cover's JPEG, 404 without cover)
- Folder-listing has_thumbnail / thumbnail_url contract
- Migration 0007 up/down (item_texts.cover_item_id)
- Orphan-upload cleanup no longer deletes note files
"""
import asyncio
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import CSRF_COOKIE_NAME
from app.infrastructure.repositories import PermissionRepository

from tests.conftest import login_as


def _upload_note(client: TestClient, folder_id: str, csrf_token: str,
                 name="note.txt", text="line one\nline two\n"):
    resp = client.post(
        "/api/uploads",
        data={"folder_id": folder_id},
        files={"file": (name, text.encode("utf-8"), "text/plain")},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _upload_photo(client: TestClient, folder_id: str, csrf_token: str,
                  test_image_bytes):
    resp = client.post(
        "/api/uploads",
        data={"folder_id": folder_id},
        files={"file": ("a.jpg", test_image_bytes, "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _put_json(client: TestClient, url: str, csrf_token: str, payload: dict):
    return client.put(
        url,
        json=payload,
        headers={"X-CSRF-Token": csrf_token},
    )


class TestNoteContentUpdate:
    """PUT /api/items/{id}/content replaces stored note content."""

    def test_update_roundtrip(self, authenticated_client, test_folder,
                              csrf_token):
        note = _upload_note(authenticated_client, test_folder, csrf_token)
        new_text = "# Rewritten\n\nbrand new body\nwith four lines"

        resp = _put_json(
            authenticated_client,
            f"/api/items/{note['id']}/content",
            csrf_token,
            {"content": new_text},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["char_count"] == len(new_text)
        assert data["line_count"] == 4

        # Content is served decrypted through /files/{id}
        file_resp = authenticated_client.get(f"/files/{note['id']}")
        assert file_resp.status_code == 200
        assert file_resp.content.decode("utf-8") == new_text

        # Counters and updated_at refresh on the item record
        item = authenticated_client.get(f"/api/items/{note['id']}").json()
        assert item["char_count"] == len(new_text)
        assert item["line_count"] == 4

    def test_update_stays_encrypted(self, authenticated_client, test_folder,
                                    csrf_token):
        from app.infrastructure.storage import get_storage

        note = _upload_note(authenticated_client, test_folder, csrf_token,
                            text="original secret")
        _put_json(
            authenticated_client,
            f"/api/items/{note['id']}/content",
            csrf_token,
            {"content": "rewritten secret"},
        )

        stored = get_storage()._get_path(note["id"], "uploads")
        raw = stored.read_bytes()
        assert b"rewritten secret" not in raw
        assert raw[:4] == b"SGE1"

    def test_update_validation(self, authenticated_client, test_folder,
                               csrf_token):
        note = _upload_note(authenticated_client, test_folder, csrf_token)

        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/content",
                         csrf_token, {"content": ""})
        assert resp.status_code == 400

        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/content",
                         csrf_token, {"content": "bad\x00binary"})
        assert resp.status_code == 400

    def test_update_size_limit(self, authenticated_client, test_folder,
                               csrf_token, monkeypatch):
        from app.application.services import item_service as item_service_mod
        monkeypatch.setattr(item_service_mod, "TEXT_MAX_SIZE", 100)

        note = _upload_note(authenticated_client, test_folder, csrf_token)
        resp = _put_json(
            authenticated_client,
            f"/api/items/{note['id']}/content",
            csrf_token,
            {"content": "x" * 200},
        )
        assert resp.status_code == 413

    def test_update_media_item_rejected(self, authenticated_client,
                                        test_folder, csrf_token,
                                        test_image_bytes):
        photo = _upload_photo(authenticated_client, test_folder, csrf_token,
                              test_image_bytes)
        resp = _put_json(
            authenticated_client,
            f"/api/items/{photo['id']}/content",
            csrf_token,
            {"content": "not a note"},
        )
        assert resp.status_code == 400

    def test_update_unknown_item_404(self, authenticated_client, csrf_token):
        resp = _put_json(
            authenticated_client,
            "/api/items/does-not-exist/content",
            csrf_token,
            {"content": "text"},
        )
        assert resp.status_code == 404

    def test_update_forbidden_for_stranger(self, authenticated_client,
                                           test_user, second_user,
                                           test_folder, csrf_token):
        note = _upload_note(authenticated_client, test_folder, csrf_token)

        with login_as(authenticated_client, second_user["username"],
                      second_user["password"]):
            stranger_csrf = authenticated_client.cookies.get(
                CSRF_COOKIE_NAME, "")
            resp = _put_json(
                authenticated_client,
                f"/api/items/{note['id']}/content",
                stranger_csrf,
                {"content": "hijack"},
            )
            assert resp.status_code == 403

        # login_as logged the client out - restore the owner session
        with login_as(authenticated_client, test_user["username"],
                      test_user["password"]):
            file_resp = authenticated_client.get(f"/files/{note['id']}")
            assert file_resp.status_code == 200
            assert file_resp.content.decode("utf-8") == "line one\nline two\n"

    def test_update_by_folder_editor(self, client, authenticated_client,
                                     db_connection, test_user, second_user,
                                     test_folder, csrf_token):
        """A folder editor can rewrite the owner's note; the envelope is
        re-encrypted with the owner's DEK (cached from their login)."""
        note = _upload_note(authenticated_client, test_folder, csrf_token)

        PermissionRepository(db_connection).grant(
            test_folder, second_user["id"], "editor", test_user["id"]
        )

        with login_as(client, second_user["username"], second_user["password"]):
            editor_csrf = client.cookies.get(CSRF_COOKIE_NAME, "")
            resp = _put_json(
                client,
                f"/api/items/{note['id']}/content",
                editor_csrf,
                {"content": "editor was here"},
            )
            assert resp.status_code == 200, resp.text

        # Owner still reads the new content
        with login_as(client, test_user["username"], test_user["password"]):
            file_resp = client.get(f"/files/{note['id']}")
            assert file_resp.status_code == 200
            assert file_resp.content.decode("utf-8") == "editor was here"


class TestNoteCover:
    """PUT /api/items/{id}/cover and the thumbnail proxy."""

    def _note_and_photo(self, authenticated_client, test_folder, csrf_token,
                        test_image_bytes):
        note = _upload_note(authenticated_client, test_folder, csrf_token)
        photo = _upload_photo(authenticated_client, test_folder, csrf_token,
                              test_image_bytes)
        return note, photo

    def test_set_and_clear_cover(self, authenticated_client, test_folder,
                                 csrf_token, test_image_bytes):
        note, photo = self._note_and_photo(
            authenticated_client, test_folder, csrf_token, test_image_bytes
        )

        # No cover: 404 + placeholder contract
        assert authenticated_client.get(
            f"/files/{note['id']}/thumbnail").status_code == 404

        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/cover",
                         csrf_token, {"cover_item_id": photo["id"]})
        assert resp.status_code == 200, resp.text

        # Item record exposes the cover reference
        item = authenticated_client.get(f"/api/items/{note['id']}").json()
        assert item["cover_item_id"] == photo["id"]

        # Folder listing publishes the grid contract
        content = authenticated_client.get(
            f"/api/folders/{test_folder}/content").json()
        notes = [i for i in content["items"]
                 if i.get("type") == "item" and i.get("item_type") == "note"]
        assert notes[0]["has_thumbnail"] is True
        assert notes[0]["thumbnail_url"].endswith(
            f"/files/{note['id']}/thumbnail")

        # Thumbnail endpoint serves the cover's JPEG
        thumb = authenticated_client.get(f"/files/{note['id']}/thumbnail")
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"
        assert thumb.content[:2] == b"\xff\xd8"

        # Clearing restores the placeholder contract
        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/cover",
                         csrf_token, {"cover_item_id": None})
        assert resp.status_code == 200
        item = authenticated_client.get(f"/api/items/{note['id']}").json()
        assert item["cover_item_id"] is None
        assert authenticated_client.get(
            f"/files/{note['id']}/thumbnail").status_code == 404

    def test_cover_target_validation(self, authenticated_client, test_folder,
                                     csrf_token):
        note = _upload_note(authenticated_client, test_folder, csrf_token)
        other_note = _upload_note(authenticated_client, test_folder,
                                  csrf_token, name="other.md",
                                  text="# other\n")

        # A note cannot be a cover
        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/cover",
                         csrf_token, {"cover_item_id": other_note["id"]})
        assert resp.status_code == 400

        # Unknown item
        resp = _put_json(authenticated_client,
                         f"/api/items/{note['id']}/cover",
                         csrf_token, {"cover_item_id": "missing-id"})
        assert resp.status_code == 400

    def test_cover_forbidden_for_stranger(self, authenticated_client,
                                          second_user, test_folder,
                                          csrf_token, test_image_bytes):
        note, photo = self._note_and_photo(
            authenticated_client, test_folder, csrf_token, test_image_bytes
        )

        with login_as(authenticated_client, second_user["username"],
                      second_user["password"]):
            stranger_csrf = authenticated_client.cookies.get(
                CSRF_COOKIE_NAME, "")
            resp = _put_json(
                authenticated_client,
                f"/api/items/{note['id']}/cover",
                stranger_csrf,
                {"cover_item_id": photo["id"]},
            )
            assert resp.status_code == 403

    def test_cover_thumbnail_accessible_to_folder_viewer(
            self, client, authenticated_client, db_connection, test_user,
            second_user, test_folder, csrf_token, test_image_bytes):
        """The cover proxy checks access to the NOTE, so a folder viewer
        gets the thumbnail even though the cover belongs to the owner."""
        note, photo = self._note_and_photo(
            authenticated_client, test_folder, csrf_token, test_image_bytes
        )
        _put_json(authenticated_client, f"/api/items/{note['id']}/cover",
                  csrf_token, {"cover_item_id": photo["id"]})

        PermissionRepository(db_connection).grant(
            test_folder, second_user["id"], "viewer", test_user["id"]
        )

        with login_as(client, second_user["username"], second_user["password"]):
            thumb = client.get(f"/files/{note['id']}/thumbnail")
            assert thumb.status_code == 200
            assert thumb.headers["content-type"] == "image/jpeg"

    def test_deleting_cover_item_clears_reference(
            self, authenticated_client, test_folder, csrf_token,
            test_image_bytes):
        note, photo = self._note_and_photo(
            authenticated_client, test_folder, csrf_token, test_image_bytes
        )
        _put_json(authenticated_client, f"/api/items/{note['id']}/cover",
                  csrf_token, {"cover_item_id": photo["id"]})

        resp = authenticated_client.delete(
            f"/api/items/{photo['id']}",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        item = authenticated_client.get(f"/api/items/{note['id']}").json()
        assert item["cover_item_id"] is None


class TestNoteMigration:
    """Revision 0007 adds and removes item_texts.cover_item_id."""

    def test_downgrade_drops_cover_column(self, tmp_path, monkeypatch):
        import app.database as db_module
        import app.config as config

        db_path = tmp_path / "mig.db"
        monkeypatch.setattr(db_module, "DATABASE_PATH", db_path)

        cfg = Config(str(config.BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(db_module.MIGRATIONS_DIR))

        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(item_texts)")}
        assert "cover_item_id" in columns
        conn.close()

        command.downgrade(cfg, "0006")

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(item_texts)")}
        assert "cover_item_id" not in columns
        conn.close()

        command.upgrade(cfg, "head")
        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(item_texts)")}
        assert "cover_item_id" in columns
        conn.close()

        db_module.get_engine().dispose()


class TestOrphanCleanupKeepsNotes:
    """Regression: cleanup_orphaned_uploads must not treat note files
    as orphans (they live under uploads/{item_id} like media)."""

    def test_cleanup_keeps_note_files(self, authenticated_client,
                                      test_folder, csrf_token):
        from app.infrastructure.services.thumbnail import (
            cleanup_orphaned_uploads,
        )
        from app.infrastructure.storage import get_storage

        note = _upload_note(authenticated_client, test_folder, csrf_token)
        storage = get_storage()
        assert storage.exists(note["id"], "uploads")

        asyncio.run(cleanup_orphaned_uploads())

        # The note's encrypted envelope and its DB row must survive.
        # (files_deleted may be non-zero: the storage singleton is shared
        # across the test session, so leftovers from earlier tests are
        # legitimately reaped.)
        assert storage.exists(note["id"], "uploads")
        item = authenticated_client.get(f"/api/items/{note['id']}")
        assert item.status_code == 200
        file_resp = authenticated_client.get(f"/files/{note['id']}")
        assert file_resp.status_code == 200
