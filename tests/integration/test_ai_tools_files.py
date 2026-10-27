'''
File:   test_ai_tools_files.py
Brief:  Integration tests for the AI chat file/metadata tools.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

Tool-level tests (no LLM involved): executors are called directly with a
``ToolContext``, mirroring the registry sanity tests in test_ai_chat.py.
Covers get_item_metadata, read_note, create_note, write_note, edit_note
and the enriched get_item output.
'''
import asyncio
import base64
import io
import json

import pytest

from app.application.services.ai_tools import (
    TOOL_REGISTRY,
    ToolContext,
    ToolError,
)
from app.infrastructure.repositories import (
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
    ItemTextRepository,
)
from app.infrastructure.services.encryption import EncryptionService
from app.infrastructure.storage import get_storage

DEK = bytes(range(32))


def _ctx(user_id: int) -> ToolContext:
    return ToolContext(user_id=user_id, dek=DEK)


def _run(coro):
    return asyncio.run(coro)


def _call(name: str, args: dict, user_id: int) -> dict:
    result = TOOL_REGISTRY[name].executor(args, _ctx(user_id))
    if asyncio.iscoroutine(result):
        result = _run(result)
    return json.loads(result)


def _make_note(db, folder_id, user_id, content, title="note.yaml",
               content_type="text/yaml") -> str:
    """Create a note item with an encrypted content blob, like an upload."""
    line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
    item_id = ItemRepository(db).create(
        item_type="note",
        folder_id=folder_id,
        user_id=user_id,
        title=title,
    )
    ItemTextRepository(db).create(
        item_id=item_id,
        content_type=content_type,
        original_name=title,
        char_count=len(content),
        line_count=line_count,
    )
    envelope = EncryptionService.encrypt_bytes(content.encode("utf-8"), DEK)
    asyncio.run(
        get_storage().upload(item_id, io.BytesIO(envelope), folder="uploads")
    )
    return item_id


def _make_media(db, folder_id, user_id, *, chunks=None,
                content_type="image/png", file_size=1234) -> str:
    item_id = ItemRepository(db).create(
        item_type="media",
        folder_id=folder_id,
        user_id=user_id,
        title="img.png",
    )
    ItemMediaRepository(db).create(
        item_id,
        media_type="image",
        original_name="img.png",
        content_type=content_type,
        width=4,
        height=4,
        thumb_width=4,
        thumb_height=4,
        file_size=file_size,
        png_text_chunks=chunks,
    )
    return item_id


# ============================================================================
# get_item_metadata
# ============================================================================

def test_get_item_metadata_summary_lists_chunk_keys(
    db_connection, test_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "a red fox, highly detailed",
                "workflow": json.dumps({"nodes": 1})},
    )
    result = _call("get_item_metadata", {"item_id": item_id}, test_user["id"])

    assert result["type"] == "media"
    assert result["file_size"] == 1234
    keys = {entry["key"]: entry for entry in result["text_chunks"]["keys"]}
    assert set(keys) == {"prompt", "workflow"}
    assert keys["prompt"]["length"] == len("a red fox, highly detailed")
    assert keys["prompt"]["preview"].startswith("a red fox")


def test_get_item_metadata_key_fetch_full_and_paged(
    db_connection, test_user, test_folder
):
    long_prompt = "x" * 7000
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": long_prompt},
    )

    full = _call(
        "get_item_metadata", {"item_id": item_id, "key": "prompt",
                              "max_chars": 6000},
        test_user["id"],
    )
    assert full["value"] == "x" * 6000
    assert full["total_chars"] == 7000
    assert full["next_offset"] == 6000

    tail = _call(
        "get_item_metadata", {"item_id": item_id, "key": "prompt",
                              "offset": 6000},
        test_user["id"],
    )
    assert tail["value"] == "x" * 1000
    assert tail["next_offset"] is None


def test_get_item_metadata_unknown_key_errors(
    db_connection, test_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "fox"},
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "get_item_metadata", {"item_id": item_id, "key": "promt"},
            test_user["id"],
        )
    assert "unknown metadata key" in str(excinfo.value)
    assert "prompt" in str(excinfo.value)


def test_get_item_metadata_on_note_reports_no_embedded_metadata(
    db_connection, test_user, test_folder
):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "a: 1\n"
    )
    result = _call("get_item_metadata", {"item_id": note_id}, test_user["id"])
    assert result["type"] == "note"
    assert result["content_type"] == "text/yaml"
    assert "read_note" in result["note"]


def test_get_item_metadata_requires_ownership(
    db_connection, test_user, second_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"], chunks={"prompt": "x"}
    )
    with pytest.raises(ToolError) as excinfo:
        _call("get_item_metadata", {"item_id": item_id}, second_user["id"])
    assert "not found" in str(excinfo.value)


def test_get_item_metadata_decodes_base64_key_fetch(
    db_connection, test_user, test_folder
):
    card = json.dumps({"name": "Taya", "creator": "Lukutar"})
    stored = base64.b64encode(card.encode("utf-8")).decode("ascii")
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"chara": stored},
    )

    raw = _call(
        "get_item_metadata",
        {"item_id": item_id, "key": "chara"},
        test_user["id"],
    )
    assert raw["value"] == stored
    assert raw["total_chars"] == len(stored)
    assert "base64_decoded" not in raw

    clean = _call(
        "get_item_metadata",
        {"item_id": item_id, "key": "chara", "decode_base64": True},
        test_user["id"],
    )
    assert clean["value"] == card
    assert clean["total_chars"] == len(card)
    assert clean["base64_decoded"] is True
    assert clean["next_offset"] is None


def test_get_item_metadata_decode_base64_pages_decoded_text(
    db_connection, test_user, test_folder
):
    # max_chars is floored at 1000: keep the decoded text longer than
    # that so the second page is actually needed.
    card = json.dumps({"bio": "x" * 1500})
    stored = base64.b64encode(card.encode("utf-8")).decode("ascii")
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"chara": stored},
    )

    first = _call(
        "get_item_metadata",
        {"item_id": item_id, "key": "chara", "decode_base64": True,
         "max_chars": 1000},
        test_user["id"],
    )
    assert first["total_chars"] == len(card)
    assert len(first["value"]) == 1000
    assert first["next_offset"] == 1000

    tail = _call(
        "get_item_metadata",
        {"item_id": item_id, "key": "chara", "decode_base64": True,
         "offset": first["next_offset"], "max_chars": 1000},
        test_user["id"],
    )
    assert first["value"] + tail["value"] == card
    assert tail["next_offset"] is None


def test_get_item_metadata_decode_base64_rejects_non_base64(
    db_connection, test_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "a red fox, highly detailed"},
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "get_item_metadata",
            {"item_id": item_id, "key": "prompt", "decode_base64": True},
            test_user["id"],
        )
    assert "not valid base64" in str(excinfo.value)


# ============================================================================
# set_item_metadata
# ============================================================================

def test_set_item_metadata_merges_and_replaces(
    db_connection, test_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "old prompt", "workflow": "{\"nodes\": 1}"},
    )

    merged = _call(
        "set_item_metadata",
        {"item_id": item_id, "chunks": {"prompt": "new prompt"}},
        test_user["id"],
    )
    assert merged["updated_keys"] == ["prompt"]
    assert merged["text_chunks"] == ["prompt", "workflow"]
    assert merged["base64_encoded"] is False
    assert "removed_keys" not in merged

    stored = ItemMediaRepository(db_connection).get_by_item_id(item_id)
    assert stored["png_text_chunks"]["prompt"] == "new prompt"
    assert stored["png_text_chunks"]["workflow"] == "{\"nodes\": 1}"

    replaced = _call(
        "set_item_metadata",
        {"item_id": item_id, "chunks": {"chara": "{}"},
         "mode": "replace_all"},
        test_user["id"],
    )
    assert replaced["removed_keys"] == ["prompt", "workflow"]
    assert replaced["text_chunks"] == ["chara"]
    stored = ItemMediaRepository(db_connection).get_by_item_id(item_id)
    assert set(stored["png_text_chunks"]) == {"chara"}


def test_set_item_metadata_encode_base64_roundtrip(
    db_connection, test_user, test_folder
):
    card = json.dumps({"name": "Taya", "data": "base64 me"})
    encoded = base64.b64encode(card.encode("utf-8")).decode("ascii")
    item_id = _make_media(db_connection, test_folder, test_user["id"])

    result = _call(
        "set_item_metadata",
        {"item_id": item_id, "chunks": {"chara": card},
         "encode_base64": True},
        test_user["id"],
    )
    assert result["base64_encoded"] is True

    stored = ItemMediaRepository(db_connection).get_by_item_id(item_id)
    assert stored["png_text_chunks"]["chara"] == encoded

    read_back = _call(
        "get_item_metadata",
        {"item_id": item_id, "key": "chara", "decode_base64": True},
        test_user["id"],
    )
    assert read_back["value"] == card


def test_set_item_metadata_validations(
    db_connection, test_user, second_user, test_folder
):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "a: 1\n"
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "set_item_metadata",
            {"item_id": note_id, "chunks": {"k": "v"}},
            test_user["id"],
        )
    assert "not a media item" in str(excinfo.value)

    item_id = _make_media(db_connection, test_folder, test_user["id"])
    with pytest.raises(ToolError) as excinfo:
        _call(
            "set_item_metadata",
            {"item_id": item_id, "chunks": {"k": 5}},
            test_user["id"],
        )
    assert "must be a string" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        _call(
            "set_item_metadata",
            {"item_id": item_id, "chunks": {"k": "v"}, "mode": "wipe"},
            test_user["id"],
        )
    assert "mode" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        _call(
            "set_item_metadata",
            {"item_id": item_id, "chunks": {"k": "v"}},
            second_user["id"],
        )
    assert "not found" in str(excinfo.value)


# ============================================================================
# read_note
# ============================================================================

def test_read_note_roundtrip_and_paging(db_connection, test_user, test_folder):
    content = "line1\nline2\nline3\n"
    note_id = _make_note(db_connection, test_folder, test_user["id"], content)

    result = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert result["content"] == content
    assert result["total_chars"] == len(content)
    assert result["next_offset"] is None
    assert result["content_type"] == "text/yaml"

    # max_chars is floored at 500, so this fits in one more page.
    paged = _call(
        "read_note", {"item_id": note_id, "offset": 6}, test_user["id"]
    )
    assert paged["content"] == "line2\nline3\n"
    assert paged["next_offset"] is None

    # A long note pages through by echoing next_offset.
    long_note = _make_note(
        db_connection, test_folder, test_user["id"], "0123456789" * 1000
    )
    first = _call("read_note", {"item_id": long_note}, test_user["id"])
    assert len(first["content"]) == 4000
    assert first["next_offset"] == 4000
    second = _call(
        "read_note", {"item_id": long_note, "offset": first["next_offset"]},
        test_user["id"],
    )
    assert second["content"] == ("0123456789" * 1000)[4000:8000]
    assert second["next_offset"] == 8000
    third = _call(
        "read_note", {"item_id": long_note, "offset": second["next_offset"]},
        test_user["id"],
    )
    assert third["content"] == ("0123456789" * 1000)[8000:]
    assert third["next_offset"] is None


def test_read_note_rejects_media(db_connection, test_user, test_folder):
    item_id = _make_media(db_connection, test_folder, test_user["id"])
    with pytest.raises(ToolError) as excinfo:
        _call("read_note", {"item_id": item_id}, test_user["id"])
    assert "not a text note" in str(excinfo.value)


def test_read_note_requires_dek(db_connection, test_user, test_folder):
    note_id = _make_note(db_connection, test_folder, test_user["id"], "x\n")
    executor = TOOL_REGISTRY["read_note"].executor
    with pytest.raises(ToolError) as excinfo:
        result = executor(
            {"item_id": note_id}, ToolContext(user_id=test_user["id"])
        )
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    assert "encryption key" in str(excinfo.value)


# ============================================================================
# create_note
# ============================================================================

def test_create_note_creates_readable_note(db_connection, test_user, test_folder):
    result = _call(
        "create_note",
        {
            "folder_id": test_folder,
            "title": "config.yaml",
            "content": "size: large\nquality: high\n",
            "content_type": "text/yaml",
        },
        test_user["id"],
    )
    assert result["content_type"] == "text/yaml"
    assert result["line_count"] == 2
    # The tool answers with the 12-char short id; recover the full row id.
    row = db_connection.execute(
        "SELECT id FROM items WHERE folder_id = ? AND user_id = ?",
        (test_folder, test_user["id"]),
    ).fetchone()
    item_id = row["id"]
    assert result["id"] == item_id[:12]

    # The stored blob decrypts back to the content under the same DEK.
    blob = asyncio.run(
        get_storage().download(item_id, folder="uploads")
    )
    plain = EncryptionService.decrypt_bytes(blob, DEK)
    assert plain.decode("utf-8") == "size: large\nquality: high\n"

    text = ItemTextRepository(db_connection).get_by_item_id(item_id)
    assert text["content_type"] == "text/yaml"
    assert text["char_count"] == len("size: large\nquality: high\n")

    # read_note accepts the short id and resolves it back.
    read_back = _call("read_note", {"item_id": result["id"]}, test_user["id"])
    assert read_back["content"] == "size: large\nquality: high\n"
    assert read_back["id"] == item_id[:12]


def test_create_note_validations(db_connection, test_user, test_folder):
    with pytest.raises(ToolError) as excinfo:
        _call(
            "create_note",
            {"folder_id": "no-such-folder", "title": "a.txt",
             "content": "x"},
            test_user["id"],
        )
    assert "folder not found" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        _call(
            "create_note",
            {"folder_id": test_folder, "title": "a.txt",
             "content": "x", "content_type": "application/zip"},
            test_user["id"],
        )
    assert "content_type" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        _call(
            "create_note",
            {"folder_id": test_folder, "title": "a.txt", "content": "a\x00b"},
            test_user["id"],
        )
    assert "NUL" in str(excinfo.value)


# ============================================================================
# write_note
# ============================================================================

def test_write_note_replace_and_append(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "hello\n"
    )

    result = _call(
        "write_note", {"item_id": note_id, "content": "world\n"},
        test_user["id"],
    )
    assert result["status"] == "ok"
    assert result["char_count"] == 6

    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "world\n"

    _call(
        "write_note",
        {"item_id": note_id, "content": "again", "mode": "append"},
        test_user["id"],
    )
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "world\nagain"

    text = ItemTextRepository(db_connection).get_by_item_id(note_id)
    assert text["char_count"] == len("world\nagain")
    assert text["line_count"] == 2


def test_write_note_rejects_media(db_connection, test_user, test_folder):
    item_id = _make_media(db_connection, test_folder, test_user["id"])
    with pytest.raises(ToolError) as excinfo:
        _call("write_note", {"item_id": item_id, "content": "x"},
              test_user["id"])
    assert "not a text note" in str(excinfo.value)


def test_write_note_rejects_bad_mode(db_connection, test_user, test_folder):
    note_id = _make_note(db_connection, test_folder, test_user["id"], "x\n")
    with pytest.raises(ToolError) as excinfo:
        _call(
            "write_note",
            {"item_id": note_id, "content": "x", "mode": "patch"},
            test_user["id"],
        )
    assert "mode" in str(excinfo.value)


# ============================================================================
# edit_note
# ============================================================================

def test_edit_note_replaces_first_occurrence(db_connection, test_user,
                                             test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"],
        "color: red\ncount: 1\n"
    )

    result = _call(
        "edit_note",
        {"item_id": note_id, "old_string": "red", "new_string": "blue"},
        test_user["id"],
    )
    assert result["status"] == "ok"
    assert result["replacements"] == 1

    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "color: blue\ncount: 1\n"

    text = ItemTextRepository(db_connection).get_by_item_id(note_id)
    assert text["char_count"] == len("color: blue\ncount: 1\n")
    assert text["line_count"] == 2


def test_edit_note_requires_unique_match(db_connection, test_user,
                                         test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "same same\n"
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": note_id, "old_string": "same", "new_string": "x"},
            test_user["id"],
        )
    assert "matches 2 times" in str(excinfo.value)
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "same same\n"


def test_edit_note_replace_all(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "same same\n"
    )
    result = _call(
        "edit_note",
        {
            "item_id": note_id,
            "old_string": "same",
            "new_string": "done",
            "replace_all": True,
        },
        test_user["id"],
    )
    assert result["replacements"] == 2
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "done done\n"


def test_edit_note_missing_fragment(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "hello\n"
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": note_id, "old_string": "world", "new_string": "x"},
            test_user["id"],
        )
    assert "not found" in str(excinfo.value)
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "hello\n"


def test_edit_note_deletes_fragment(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "keep DROP keep\n"
    )
    _call(
        "edit_note",
        {"item_id": note_id, "old_string": "DROP ", "new_string": ""},
        test_user["id"],
    )
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == "keep keep\n"


def test_edit_note_short_id_and_multiline(db_connection, test_user,
                                          test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"],
        "# Title\n\nold paragraph\n\ntail\n"
    )
    result = _call(
        "edit_note",
        {
            "item_id": note_id[:12],
            "old_string": "old paragraph",
            "new_string": "new paragraph\nwith two lines",
        },
        test_user["id"],
    )
    assert result["status"] == "ok"
    read_back = _call("read_note", {"item_id": note_id}, test_user["id"])
    assert read_back["content"] == (
        "# Title\n\nnew paragraph\nwith two lines\n\ntail\n"
    )


def test_edit_note_validations(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "hello\n"
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": note_id, "old_string": "", "new_string": "x"},
            test_user["id"],
        )
    assert "old_string" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": note_id, "old_string": "hello",
             "new_string": "hello"},
            test_user["id"],
        )
    assert "identical" in str(excinfo.value)


def test_edit_note_rejects_media(db_connection, test_user, test_folder):
    item_id = _make_media(db_connection, test_folder, test_user["id"])
    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": item_id, "old_string": "x", "new_string": "y"},
            test_user["id"],
        )
    assert "not a text note" in str(excinfo.value)


def test_edit_note_requires_ownership(db_connection, test_user, second_user,
                                      test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "hello\n"
    )
    with pytest.raises(ToolError) as excinfo:
        _call(
            "edit_note",
            {"item_id": note_id, "old_string": "hello", "new_string": "x"},
            second_user["id"],
        )
    assert "not found" in str(excinfo.value)


# ============================================================================
# get_item enrichment
# ============================================================================

def test_get_item_lists_chunk_names_and_file_size(
    db_connection, test_user, test_folder
):
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "fox", "workflow": "{}"},
    )
    result = _call("get_item", {"item_id": item_id}, test_user["id"])
    assert result["file_size"] == 1234
    assert result["text_chunks"] == ["prompt", "workflow"]
    # Names only: values must not leak into get_item output.
    assert "fox" not in json.dumps(result)


def test_get_item_note_reports_counters(db_connection, test_user, test_folder):
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "a\nb\nc\n"
    )
    result = _call("get_item", {"item_id": note_id}, test_user["id"])
    assert result["char_count"] == 6
    assert result["line_count"] == 3
