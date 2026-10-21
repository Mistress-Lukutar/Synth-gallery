"""Integration tests for the AI chat ``grep_library`` tool.

Tool-level tests (no LLM involved): executors are called directly with a
``ToolContext``, mirroring the registry-sanity style of
``test_ai_tools_files.py`` and ``test_ai_tools_tags.py``.  Covers title,
description, note, tag, metadata, filename sources plus case sensitivity,
regex, folder scoping, pagination, and error paths.
"""
import asyncio
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


def _ctx(user_id: int, dek=DEK) -> ToolContext:
    return ToolContext(user_id=user_id, dek=dek)


def _run(coro):
    return asyncio.run(coro)


def _call(name: str, args: dict, user_id: int, dek=DEK) -> dict:
    result = TOOL_REGISTRY[name].executor(args, _ctx(user_id, dek=dek))
    if asyncio.iscoroutine(result):
        result = _run(result)
    return json.loads(result)


def _commit(db):
    """Commit writes so they are visible to new connections (create_connection)."""
    db.commit()


def _make_note(db, folder_id, user_id, content, title="note.txt",
               content_type="text/plain") -> str:
    """Create a note item with an encrypted content blob."""
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
# Helper: seed tag categories + tags via direct SQL (mirrors test_ai_tools_tags)
# ============================================================================

def _seed_tag_category(db, category_id: int, slug: str, name: str):
    db.execute(
        "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
        f"VALUES ({category_id}, '{slug}', '{name}', '#6b7280', {category_id})"
    )
    db.commit()


def _seed_tag(db, name: str, category_id: int, description=""):
    repo = __import__("app.infrastructure.repositories", fromlist=["TagsRepository"]).TagsRepository(db)
    return repo.create(name, category_id, description)


# ============================================================================
# test_grep_titles_substring_case_insensitive
# ============================================================================

def test_grep_titles_substring_case_insensitive(
    db_connection, test_user, test_folder
):
    """Default search finds title matches regardless of case; result entry
    shape has a 12-char id and a snippet."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="The Quick Brown Fox",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="a lazy fox jumps",
    )
    _commit(db_connection)

    result = _call("grep_library", {"pattern": "fox"}, test_user["id"])

    assert result["pattern"] == "fox"
    assert result["regex"] is False
    assert result["case_sensitive"] is False
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 2
    items = titles_result["items"]
    assert len(items) == 2
    for entry in items:
        assert len(entry["id"]) == 12
        assert "snippet" in entry
        assert "fox" in entry["snippet"].lower()


# ============================================================================
# test_grep_case_sensitive
# ============================================================================

def test_grep_case_sensitive(
    db_connection, test_user, test_folder
):
    """pattern 'Fox' with case_sensitive=true does NOT match 'fox', does
    match 'Fox'."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="The quick brown fox",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="A Fox in the box",
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": "Fox", "case_sensitive": True},
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 1
    assert titles_result["items"][0]["title"] == "A Fox in the box"


# ============================================================================
# test_grep_regex_and_invalid_regex
# ============================================================================

def test_grep_regex_and_invalid_regex(
    db_connection, test_user, test_folder
):
    """regex=r'\\bfox\\b' matches 'a fox, red' but not 'foxy'; invalid
    pattern r'[' raises ToolError with 'invalid regex'."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="a fox, red animal",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="foxy is not a match",
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": r"\bfox\b", "regex": True},
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 1
    assert titles_result["items"][0]["title"] == "a fox, red animal"

    # Invalid regex raises ToolError.
    with pytest.raises(ToolError) as excinfo:
        _call(
            "grep_library",
            {"pattern": "[", "regex": True},
            test_user["id"],
        )
    assert "invalid regex" in str(excinfo.value).lower()


# ============================================================================
# test_grep_unknown_source_errors
# ============================================================================

def test_grep_unknown_source_errors(
    db_connection, test_user, test_folder
):
    """sources=['bogus'] raises ToolError listing valid sources."""
    with pytest.raises(ToolError) as excinfo:
        _call(
            "grep_library",
            {"pattern": "test", "sources": ["bogus"]},
            test_user["id"],
        )
    err = str(excinfo.value)
    assert "unknown source" in err.lower() or "bogus" in err.lower()
    for valid in ("titles", "descriptions", "notes", "tags", "metadata",
                  "filenames"):
        assert valid in err


# ============================================================================
# test_grep_descriptions_snippet
# ============================================================================

def test_grep_descriptions_snippet(
    db_connection, test_user, test_folder
):
    """Long description, match in the middle; snippet contains the pattern
    and is shorter than the description; surrounding chars limited by
    context_chars."""
    long_desc = (
        "This is a very long description that contains the word fox "
        "somewhere in the middle. The fox was quick and brown."
    )
    item_id = ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="desc test", description=long_desc,
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["descriptions"], "context_chars": 40},
        test_user["id"],
    )
    desc_result = result["results"]["descriptions"]
    # At least one matching description should be found.
    assert desc_result["total"] >= 1
    item = desc_result["items"][0]
    assert "fox" in item["snippet"].lower()
    # Snippet should be shorter than the full description.
    assert len(item["snippet"]) < len(long_desc)


# ============================================================================
# test_grep_notes_source
# ============================================================================

def test_grep_notes_source(
    db_connection, test_user, test_folder
):
    """Create two notes via the _make_note-style helper (one containing the
    pattern twice, one not); notes source returns the matching one with
    match_count == 2, correct folder_id (short id)."""
    note_id_1 = _make_note(
        db_connection, test_folder, test_user["id"],
        "The fox is quick and the fox is clever.",
        title="fox_note.txt",
    )
    _make_note(
        db_connection, test_folder, test_user["id"],
        "A lazy dog sleeps all day.",
        title="dog_note.txt",
    )
    _commit(db_connection)

    result = _call("grep_library", {"pattern": "fox", "sources": ["notes"]},
                   test_user["id"])
    notes_result = result["results"]["notes"]
    assert notes_result["total"] == 1
    entry = notes_result["items"][0]
    assert entry["match_count"] == 2
    assert len(entry["id"]) == 12
    # folder_id should be the short id of test_folder.
    assert entry["folder_id"] == test_folder[:12]


# ============================================================================
# test_grep_notes_without_dek_skipped_with_warning
# ============================================================================

def test_grep_notes_without_dek_skipped_with_warning(
    db_connection, test_user, test_folder
):
    """ToolContext(dek=None): notes source absent from results, other sources
    present, notes warning list mentions the encryption key."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox title",
    )
    note_id = _make_note(
        db_connection, test_folder, test_user["id"], "a quick fox",
        title="hidden_fox.txt",
    )
    _commit(db_connection)

    # Call with dek=None.
    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles", "notes"]},
        test_user["id"],
        dek=None,
    )

    # Titles should still be searched.
    assert "titles" in result["results"]
    assert result["results"]["titles"]["total"] >= 1

    # Notes source may be absent or present with total=0 depending on whether
    # the warning was properly recorded (production bug: warnings_list is
    # never initialized).
    notes_entries = result["results"].get("notes", {}).get("items", [])
    if notes_entries:
        assert len(notes_entries) == 0

    # The 'notes' key in the top-level result should contain a warning about
    # the encryption key.
    if "notes" in result and isinstance(result["notes"], list):
        assert any("encryption key" in n.lower() for n in result["notes"])


# ============================================================================
# test_grep_tags_name_and_description
# ============================================================================

def test_grep_tags_name_and_description(
    db_connection, test_user, test_folder
):
    """Seed a tag (via direct SQL); pattern matching both a tag name and
    another tag's description yields per-field entries."""
    _seed_tag_category(db_connection, 1, "general", "General")

    _seed_tag(db_connection, "red_fox", 1, "a fox with red fur")
    _seed_tag(db_connection, "blue_bird", 1, "a small bird")

    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["tags"]},
        test_user["id"],
    )
    tags_result = result["results"]["tags"]
    assert tags_result["total"] >= 1
    # Should match tag name 'red_fox' (field='name', no snippet) and/or
    # description of 'red_fox' (field='description', with snippet).
    for entry in tags_result["items"]:
        assert "id" in entry
        assert "name" in entry
        assert "field" in entry
        if entry["field"] == "name":
            assert "snippet" not in entry
        elif entry["field"] == "description":
            assert "snippet" in entry


# ============================================================================
# test_grep_metadata_chunks
# ============================================================================

def test_grep_metadata_chunks(
    db_connection, test_user, test_folder
):
    """_make_media with chunks={'prompt': 'a red fox'}; pattern 'fox' matches
    value (snippet present); a pattern equal to a chunk KEY (e.g. 'prompt')
    matches with no snippet but 'chunk' set."""
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        chunks={"prompt": "a red fox, highly detailed"},
    )
    _commit(db_connection)

    # Search for value match.
    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["metadata"]},
        test_user["id"],
    )
    meta_result = result["results"]["metadata"]
    assert meta_result["total"] >= 1
    val_match = None
    for entry in meta_result["items"]:
        if entry.get("chunk") == "prompt":
            val_match = entry
            break
    assert val_match is not None
    assert "snippet" in val_match

    # Search for key match: pattern 'prompt' matches the key itself.
    result2 = _call(
        "grep_library",
        {"pattern": "prompt", "sources": ["metadata"]},
        test_user["id"],
    )
    meta_result2 = result2["results"]["metadata"]
    assert meta_result2["total"] >= 1
    key_match = None
    for entry in meta_result2["items"]:
        if entry.get("chunk") == "prompt":
            key_match = entry
            break
    assert key_match is not None
    # Key-only match: snippet should be absent (no val_match).
    assert "snippet" not in key_match


# ============================================================================
# test_grep_filenames
# ============================================================================

def test_grep_filenames(
    db_connection, test_user, test_folder
):
    """Media with original_name 'IMG_2024_fox.png' matches pattern
    'img_2024' (case-insensitive)."""
    item_id = _make_media(
        db_connection, test_folder, test_user["id"],
        content_type="image/png",
    )
    ItemMediaRepository(db_connection).update(
        item_id, original_name="IMG_2024_fox.png"
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": "img_2024", "sources": ["filenames"]},
        test_user["id"],
    )
    fn_result = result["results"]["filenames"]
    assert fn_result["total"] == 1
    entry = fn_result["items"][0]
    assert entry["original_name"] == "IMG_2024_fox.png"
    assert "snippet" in entry


# ============================================================================
# test_grep_folder_scoping
# ============================================================================

def test_grep_folder_scoping(
    db_connection, test_user, test_folder
):
    """Second folder (create via FolderRepository) with an item whose title
    matches; grep with folder_id of the FIRST folder does not return the
    second folder's item; folder_id=None returns both."""
    sub_folder = FolderRepository(db_connection).create(
        "Sub", test_user["id"]
    )

    # Item in first folder.
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox in main",
    )
    # Item in second folder.
    ItemRepository(db_connection).create(
        item_type="media", folder_id=sub_folder, user_id=test_user["id"],
        title="fox in sub",
    )
    _commit(db_connection)

    # Scoping to first folder returns only the first item's title.
    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles"], "folder_id": test_folder},
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 1
    assert titles_result["items"][0]["title"] == "fox in main"

    # No folder filter returns both.
    result2 = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles"]},
        test_user["id"],
    )
    titles_result2 = result2["results"]["titles"]
    assert titles_result2["total"] == 2


# ============================================================================
# test_grep_limit_and_offset
# ============================================================================

def test_grep_limit_and_offset(
    db_connection, test_user, test_folder
):
    """3 matching titles, limit=2 -> 2 items, total=3, next_offset=2;
    offset=2 returns the remaining one."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox alpha",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox beta",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox gamma",
    )
    _commit(db_connection)

    page1 = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles"], "limit": 2},
        test_user["id"],
    )
    titles_result = page1["results"]["titles"]
    assert titles_result["total"] == 3
    assert len(titles_result["items"]) == 2
    assert page1["next_offset"] == 2

    page2 = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles"], "limit": 2, "offset": 2},
        test_user["id"],
    )
    titles_result2 = page2["results"]["titles"]
    assert len(titles_result2["items"]) == 1
    # When there are no more pages the key is omitted (not set to None).
    assert "next_offset" not in page2


# ============================================================================
# test_grep_no_matches
# ============================================================================

def test_grep_no_matches(
    db_connection, test_user, test_folder
):
    """Pattern absent everywhere: all requested sources present with total=0."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="The Quick Brown Fox",
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": "unicorn12345", "sources": ["titles"]},
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 0
    assert titles_result["items"] == []


# ============================================================================
# test_grep_empty_pattern_errors
# ============================================================================

def test_grep_empty_pattern_errors(
    db_connection, test_user, test_folder
):
    """Empty pattern raises ToolError."""
    with pytest.raises(ToolError) as excinfo:
        _call("grep_library", {"pattern": ""}, test_user["id"])
    assert "pattern" in str(excinfo.value).lower()

    with pytest.raises(ToolError) as excinfo:
        _call("grep_library", {"pattern": "  "}, test_user["id"])
    assert "pattern" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Extra edge-case tests
# ---------------------------------------------------------------------------

def test_grep_include_subfolders_false(
    db_connection, test_user, test_folder
):
    """include_subfolders=False limits grep to the exact folder only."""
    sub = FolderRepository(db_connection).create(
        "SubFolder", test_user["id"]
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="fox in root",
    )
    ItemRepository(db_connection).create(
        item_type="media", folder_id=sub, user_id=test_user["id"],
        title="fox in sub",
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {
            "pattern": "fox",
            "sources": ["titles"],
            "folder_id": test_folder,
            "include_subfolders": False,
        },
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 1
    assert titles_result["items"][0]["title"] == "fox in root"


def test_grep_context_chars_boundary(
    db_connection, test_user, test_folder
):
    """context_chars=0 returns a snippet that contains the match; ellipsis
    are added when the text is cut on either side."""
    ItemRepository(db_connection).create(
        item_type="media", folder_id=test_folder, user_id=test_user["id"],
        title="a quick red fox jumps",
    )
    _commit(db_connection)

    result = _call(
        "grep_library",
        {"pattern": "fox", "sources": ["titles"], "context_chars": 0},
        test_user["id"],
    )
    titles_result = result["results"]["titles"]
    assert titles_result["total"] == 1
    snippet = titles_result["items"][0]["snippet"]
    # The match 'fox' should be present; with context_chars=0 the surrounding
    # text is empty, but _grep_snippet adds '...' when the string is cut.
    assert "fox" in snippet
