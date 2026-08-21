"""Integration tests for text-note uploads (items.type = 'note').

Covers:
- Upload of txt/md/json/csv/yaml (incl. octet-stream extension inference)
- Content roundtrip through the encrypted /files/{id} endpoint
- Validation: binary rejection, unknown types, size limit
- Folder listing, metadata API, thumbnails (404), albums, batch download
"""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import CSRF_COOKIE_NAME


def _upload(client: TestClient, folder_id: str, csrf_token: str,
            name: str, content: bytes, mime: str):
    return client.post(
        "/api/uploads",
        data={"folder_id": folder_id},
        files={"file": (name, content, mime)},
        headers={"X-CSRF-Token": csrf_token},
    )


class TestNoteUpload:
    """Uploading text files creates note items."""

    def test_upload_txt(self, authenticated_client, test_folder, csrf_token):
        text = "hello note\nsecond line"
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "note.txt", text.encode("utf-8"), "text/plain",
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["type"] == "note"
        assert data["content_type"] == "text/plain"
        assert data["char_count"] == len(text)
        assert data["line_count"] == 2

    @pytest.mark.parametrize("name,mime", [
        ("doc.md", "text/markdown"),
        ("data.json", "application/json"),
        ("table.csv", "text/csv"),
        ("conf.yaml", "application/octet-stream"),   # extension inference
        ("conf.yml", "application/octet-stream"),    # extension inference
    ])
    def test_upload_note_formats(
        self, authenticated_client, test_folder, csrf_token, name, mime
    ):
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            name, b"key: value\n", mime,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["type"] == "note"

    def test_note_content_roundtrip(
        self, authenticated_client, test_folder, csrf_token
    ):
        text = "# Title\n\n- one\n- two\n"
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "readme.md", text.encode("utf-8"), "text/markdown",
        )
        assert resp.status_code == 200
        item_id = resp.json()["id"]

        file_resp = authenticated_client.get(f"/files/{item_id}")
        assert file_resp.status_code == 200
        assert file_resp.headers["content-type"].startswith("text/markdown")
        assert file_resp.content.decode("utf-8") == text

    def test_note_stored_encrypted(self, authenticated_client, test_folder,
                                   csrf_token):
        """The stored envelope must not contain the plaintext."""
        from app.infrastructure.storage import get_storage

        text = "very secret prompt text"
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "prompt.txt", text.encode("utf-8"), "text/plain",
        )
        item_id = resp.json()["id"]

        stored = get_storage()._get_path(item_id, "uploads")
        raw = stored.read_bytes()
        assert text.encode("utf-8") not in raw
        assert raw[:4] == b"SGE1"

    def test_upload_rejects_binary_as_text(
        self, authenticated_client, test_folder, csrf_token
    ):
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "fake.txt", b"\x00\x01\x02binary\x00", "text/plain",
        )
        assert resp.status_code == 400

    def test_upload_rejects_unknown_type(
        self, authenticated_client, test_folder, csrf_token
    ):
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "program.exe", b"MZ...", "application/octet-stream",
        )
        assert resp.status_code == 400

    def test_upload_rejects_empty(
        self, authenticated_client, test_folder, csrf_token
    ):
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "empty.txt", b"", "text/plain",
        )
        assert resp.status_code == 400

    def test_upload_size_limit(
        self, authenticated_client, test_folder, csrf_token, monkeypatch
    ):
        from app.application.services import item_service as item_service_mod
        monkeypatch.setattr(item_service_mod, "TEXT_MAX_SIZE", 100)

        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "big.txt", b"x" * 200, "text/plain",
        )
        assert resp.status_code == 413

    def test_cp1251_decoding(self, authenticated_client, test_folder,
                             csrf_token):
        """Legacy cp1251 text decodes and roundtrips as UTF-8."""
        original = "привет, мир"
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "legacy.txt", original.encode("cp1251"), "text/plain",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["encoding"] == "cp1251"

        file_resp = authenticated_client.get(f"/files/{data['id']}")
        assert file_resp.content.decode("utf-8") == original


class TestNoteIntegration:
    """Notes flow through the polymorphic item pipeline."""

    def _upload_note(self, authenticated_client, test_folder, csrf_token):
        resp = _upload(
            authenticated_client, test_folder, csrf_token,
            "note.txt", "line one\nline two\n", "text/plain",
        )
        assert resp.status_code == 200
        return resp.json()

    def test_note_in_folder_content(
        self, authenticated_client, test_folder, csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        resp = authenticated_client.get(f"/api/folders/{test_folder}/content")
        assert resp.status_code == 200

        notes = [
            i for i in resp.json()["items"]
            if i.get("type") == "item" and i.get("item_type") == "note"
        ]
        assert len(notes) == 1
        assert notes[0]["id"] == note["id"]
        assert notes[0]["has_thumbnail"] is False

    def test_note_metadata_api(
        self, authenticated_client, test_folder, csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        resp = authenticated_client.get(f"/api/items/{note['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "note"
        assert data["content_type"] == "text/plain"
        assert data["original_name"] == "note.txt"
        assert data["line_count"] == 2

    def test_note_thumbnail_404(
        self, authenticated_client, test_folder, csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        resp = authenticated_client.get(f"/files/{note['id']}/thumbnail")
        assert resp.status_code == 404

    def test_note_in_album(
        self, authenticated_client, test_folder, test_image_bytes,
        csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        photo = authenticated_client.post(
            "/api/uploads",
            data={"folder_id": test_folder},
            files={"file": ("a.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token},
        ).json()

        create = authenticated_client.post(
            "/api/albums",
            json={
                "name": "Mixed Album",
                "folder_id": test_folder,
                "item_ids": [photo["id"], note["id"]],
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert create.status_code == 200
        album_id = create.json()["album_id"]

        items = authenticated_client.get(f"/api/albums/{album_id}").json()
        ids = [item["id"] for item in items["items"]]
        assert note["id"] in ids and photo["id"] in ids

    def test_note_batch_download_zip(
        self, authenticated_client, test_folder, test_image_bytes, csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        photo = authenticated_client.post(
            "/api/uploads",
            data={"folder_id": test_folder},
            files={"file": ("a.jpg", test_image_bytes, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token},
        ).json()

        resp = authenticated_client.post(
            "/api/items/batch-download",
            json={
                "item_ids": [photo["id"], note["id"]],
                "album_ids": [],
                "options": {"format": "jpeg"},
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            assert any("note.txt" in n for n in names)
            assert any("a.jpg" in n for n in names)
            assert zf.read([n for n in names if "note.txt" in n][0]) == (
                b"line one\nline two\n"
            )

    def test_note_single_download(
        self, authenticated_client, test_folder, csrf_token
    ):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        resp = authenticated_client.post(
            "/api/items/batch-download",
            json={"item_ids": [note["id"]], "album_ids": []},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "note.txt" in resp.headers.get("content-disposition", "")

    def test_note_delete(self, authenticated_client, test_folder, csrf_token):
        note = self._upload_note(authenticated_client, test_folder, csrf_token)
        resp = authenticated_client.delete(
            f"/api/items/{note['id']}",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        gone = authenticated_client.get(f"/api/items/{note['id']}")
        assert gone.status_code == 404
