"""AI tool registry - the tools the chat model may call.

Every executor opens its own database connection (closed in ``finally``),
scopes every query by the requesting user and returns a plain-text JSON
result for the model. Long results are truncated by the chat orchestrator,
not here.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import uuid
from typing import Optional

from fastapi import HTTPException

from ....config import TAG_DESCRIPTION_MAX_CHARS, TEXT_MAX_SIZE
from ....database import create_connection
from ....infrastructure.repositories import (
    AUDIT_CHECKS,
    AlbumRepository,
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
    ItemTextRepository,
    PermissionRepository,
    TagCooccurrenceRepository,
    TagFeedbackRepository,
    TagImplicationRepository,
    TagMutexRepository,
    TagsRepository,
)
from ....infrastructure.services.encryption import EncryptionService
from ....infrastructure.services.metadata import extract_image_exif
from ....infrastructure.storage import get_storage
from ..album_service import AlbumService
from ..ai_memory_service import (
    MEMORY_INDEX_STEM,
    add_index_entry,
    build_frontmatter,
    ensure_index_note,
    find_index_notes,
    find_section,
    get_memory_config,
    parse_frontmatter,
    validate_section_description,
    validate_section_name,
)
from ..folder_service import FolderService
from ..item_service import ItemService
from ..item_types import is_known_item_type
from ..tag_service import TagService
from ..tag_suggestion_service import TagSuggestionService
from .base import ToolContext, ToolDef, ToolError, VisionRequestSignal
from .web import FETCH_URL_DEF, WEB_SEARCH_DEF

MEDIA_TYPE = "media"
NOTE_TYPE = "note"

# Canonical note content types accepted by create_note (the same set the
# upload pipeline infers from file extensions).
NOTE_CONTENT_TYPES = (
    "text/plain",
    "text/markdown",
    "text/yaml",
    "application/json",
    "text/csv",
)

# Token-economy caps: tool results are replayed on every LLM call of the
# turn, so reads are preview-first and paged instead of dumped in full.
_METADATA_PREVIEW_CHARS = 200
_METADATA_MAX_KEYS = 10
_METADATA_KEY_MAX_CHARS = 6000
_NOTE_READ_DEFAULT_CHARS = 4000
_NOTE_READ_MAX_CHARS = 6000
# get_tag_info batch size: keeps the JSON answer safely under the
# orchestrator's TOOL_RESULT_LIMIT even when every tag carries implications.
_TAG_INFO_MAX_NAMES = 50
# set_tag_descriptions batch size: one approval card covers the whole batch.
_TAG_DESC_MAX_UPDATES = 25
# Live EXIF probing decrypts the whole image into memory; skip huge files.
_EXIF_PROBE_MAX_BYTES = 256 * 1024 * 1024
# set_item_metadata guards: chunk keys stay short, the whole dict must not
# bloat the item_media row (ComfyUI workflows are big but not THIS big).
_METADATA_SET_KEY_MAX_CHARS = 200
_METADATA_SET_TOTAL_MAX_CHARS = 4 * 1024 * 1024
# grep_library: matching is done in Python (uniform for substring/regex);
# notes are decrypted per blob, so scanning is capped.
_GREP_SOURCES = ("titles", "descriptions", "notes", "tags", "metadata",
                 "filenames")
_GREP_DEFAULT_CONTEXT_CHARS = 60
_GREP_CONTEXT_MAX_CHARS = 200
_GREP_MAX_NOTES = 300
_GREP_NOTE_MAX_BYTES = 512 * 1024

# Memory tools are global: they bypass agent allow-lists and are added to
# every chat's effective tool set while memory is enabled in the user's
# AI settings (see ai_memory_service / AiChatService._effective_tools).
MEMORY_TOOL_NAMES = frozenset({
    "read_memory",
    "create_memory_section",
    "write_memory",
    "edit_memory",
})


# ============================================================================
# Helpers
# ============================================================================

def _normalize_tag_name(name) -> str:
    """Normalize a tag name exactly like TagService.create_tag does."""
    return str(name or "").lower().strip().replace(" ", "_")


def _clamp_int(value, minimum: int, maximum: int, default: int) -> int:
    """Clamp an optional integer argument into [minimum, maximum]."""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _http_to_tool_error(exc: HTTPException) -> ToolError:
    """Convert a service-layer HTTPException into a ToolError."""
    return ToolError(str(exc.detail))


# ============================================================================
# Short id helpers
# ============================================================================

# Tool outputs carry 12-char id prefixes instead of full 36-char UUIDs to
# save context tokens; every id-accepting tool resolves prefixes back to
# full ids (an exact full id is always accepted too). Tag ids are small
# integers and are never shortened.
ID_SHORT_LEN = 12


def short_id(value) -> str:
    """Display form of an item/folder/album id: a 12-char prefix."""
    return str(value or "")[:ID_SHORT_LEN]


def _pick_prefix_match(matches: list[dict], value: str, label: str) -> str:
    """Return the single full id among prefix candidates, else ToolError.

    ``label`` is the entity kind ("item", "folder", "album") used in the
    error text.
    """
    if not matches:
        raise ToolError(f"{label} not found: {value}")
    if len(matches) == 1:
        return matches[0]["id"]
    listed = ", ".join(
        "{} ({})".format(m.get("name") or m.get("title") or m["id"], m["id"])
        for m in matches[:10]
    )
    raise ToolError(
        f"ambiguous {label} id prefix '{value}'; pass more characters. "
        f"Candidates: {listed}"
    )


# A folder (or album via its folder) is visible to the resolver when the
# user owns it or holds an explicit viewer/editor permission — the same
# visibility the listing tools apply.
_SHARED_FOLDER_SQL = (
    "EXISTS (SELECT 1 FROM folder_permissions p WHERE p.folder_id = "
    "folders.id AND p.user_id = ? AND p.permission IN ('viewer', 'editor'))"
)


def _resolve_item_id(db, user_id: int, value, label: str = "item_id") -> str:
    """Resolve a full id or a unique short prefix to an owned item id."""
    value = str(value or "").strip()
    if not value:
        raise ToolError(f"{label} is required")
    if len(value) >= 36:
        row = db.execute(
            "SELECT id FROM items WHERE id = ? AND user_id = ?",
            (value, user_id),
        ).fetchone()
        if row is None:
            raise ToolError(f"item not found: {value}")
        return value
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT id, title FROM items WHERE user_id = ? AND id LIKE ?",
            (user_id, value + "%"),
        ).fetchall()
    ]
    return _pick_prefix_match(rows, value, "item")


def _resolve_folder_id(
    db, user_id: int, value, *, owner_only: bool = False,
    label: str = "folder_id",
) -> str:
    """Resolve a full id or a unique short prefix to a visible folder id.

    ``owner_only`` limits the search to folders the user owns; otherwise
    shared viewer/editor folders resolve too.
    """
    value = str(value or "").strip()
    if not value:
        raise ToolError(f"{label} is required")
    if owner_only:
        scope, params = "user_id = ?", [user_id]
    else:
        scope = f"user_id = ? OR {_SHARED_FOLDER_SQL}"
        params = [user_id, user_id]
    if len(value) >= 36:
        row = db.execute(
            f"SELECT id FROM folders WHERE id = ? AND ({scope})",
            (value, *params),
        ).fetchone()
        if row is None:
            raise ToolError(f"folder not found: {value}")
        return value
    rows = [
        dict(r)
        for r in db.execute(
            f"SELECT id, name FROM folders WHERE id LIKE ? AND ({scope})",
            (value + "%", *params),
        ).fetchall()
    ]
    return _pick_prefix_match(rows, value, "folder")


def _resolve_album_id(db, user_id: int, value, label: str = "album_id") -> str:
    """Resolve a full id or a unique short prefix to an album id visible
    through its folder (owned or shared viewer/editor)."""
    value = str(value or "").strip()
    if not value:
        raise ToolError(f"{label} is required")
    visible = (
        "f.user_id = ? OR EXISTS (SELECT 1 FROM folder_permissions p "
        "WHERE p.folder_id = f.id AND p.user_id = ? "
        "AND p.permission IN ('viewer', 'editor'))"
    )
    if len(value) >= 36:
        row = db.execute(
            f"SELECT a.id FROM albums a JOIN folders f ON f.id = a.folder_id "
            f"WHERE a.id = ? AND ({visible})",
            (value, user_id, user_id),
        ).fetchone()
        if row is None:
            raise ToolError(f"album not found: {value}")
        return value
    rows = [
        dict(r)
        for r in db.execute(
            f"SELECT a.id, a.name FROM albums a "
            f"JOIN folders f ON f.id = a.folder_id "
            f"WHERE a.id LIKE ? AND ({visible})",
            (value + "%", user_id, user_id),
        ).fetchall()
    ]
    return _pick_prefix_match(rows, value, "album")


def _resolve_item_id_list(db, user_id: int, values, label: str) -> list[str]:
    """Resolve a list of ids/prefixes; duplicates keep their positions."""
    if not isinstance(values, list) or not values:
        raise ToolError(f"{label} must be a non-empty list")
    return [_resolve_item_id(db, user_id, v, label=label) for v in values]


def _tag_service(db) -> TagService:
    return TagService(
        tags_repo=TagsRepository(db),
        implication_repo=TagImplicationRepository(db),
        cooccurrence_repo=TagCooccurrenceRepository(db),
        mutex_repo=TagMutexRepository(db),
    )


def _folder_service(db) -> FolderService:
    return FolderService(
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db),
    )


def _album_service(db) -> AlbumService:
    return AlbumService(
        album_repository=AlbumRepository(db),
        item_repository=ItemRepository(db),
        folder_repository=FolderRepository(db),
        permission_repository=PermissionRepository(db),
        item_media_repository=ItemMediaRepository(db),
    )


def _item_service(db) -> ItemService:
    return ItemService(
        item_repository=ItemRepository(db),
        item_media_repository=ItemMediaRepository(db),
    )


def _get_owned_item(db, item_id, ctx: ToolContext) -> dict:
    """Return the item row when it exists and belongs to the caller.

    ``item_id`` may be a full id or a unique short prefix (resolved here).
    """
    item_id = _resolve_item_id(db, ctx.user_id, item_id)
    item = ItemRepository(db).get_by_id(item_id)
    if item is None or item.get("user_id") != ctx.user_id:
        raise ToolError(f"item not found: {item_id}")
    return item


def _resolve_unknown_tags(
    tags_repo: TagsRepository, tag_names
) -> tuple[list[str], dict[str, list[dict]]]:
    """Normalize names and split them into known (with rows) and unknown."""
    normalized = [n for n in (_normalize_tag_name(n) for n in tag_names) if n]
    unique = list(dict.fromkeys(normalized))
    found = tags_repo.get_by_names(unique)
    unknown = [name for name in unique if not found.get(name)]
    return unique, found


def _resolve_category_id(tags_repo: TagsRepository, category_id) -> int:
    """Validate an optional category id, returning it as int."""
    try:
        cat_id = int(category_id)
    except (TypeError, ValueError):
        raise ToolError(f"invalid category_id: {category_id}")
    if tags_repo.get_category_by_id(cat_id) is None:
        raise ToolError(f"tag category {cat_id} not found")
    return cat_id


def _require_dek(ctx: ToolContext) -> bytes:
    """Return the caller's DEK or fail; file tools cannot work without it."""
    if not ctx.dek:
        raise ToolError("encryption key not available")
    return ctx.dek


def _validate_note_content(content) -> tuple[str, int]:
    """Validate note text like the upload pipeline; return (text, lines)."""
    if not isinstance(content, str) or not content.strip():
        raise ToolError("content must be a non-empty string")
    if "\x00" in content:
        raise ToolError("content contains NUL bytes; not a text file")
    if len(content.encode("utf-8")) > TEXT_MAX_SIZE:
        raise ToolError(
            f"content exceeds the {TEXT_MAX_SIZE // (1024 * 1024)} MB limit"
        )
    line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
    return content, line_count


async def _read_note_text(item_id: str, dek: bytes) -> str:
    """Download, decrypt and decode a note's stored blob (always UTF-8)."""
    blob = await get_storage().download(item_id, folder="uploads")
    plain = EncryptionService.decrypt_bytes(blob, dek)
    return plain.decode("utf-8", errors="replace")


async def _write_note_blob(item_id: str, content: str, dek: bytes) -> None:
    """Encrypt note text with ``dek`` and store it as the item's blob."""
    envelope = EncryptionService.encrypt_bytes(content.encode("utf-8"), dek)
    await get_storage().upload(item_id, io.BytesIO(envelope), folder="uploads")


def _count_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1)


async def _probe_exif(item_id: str, media: dict) -> dict:
    """Decrypt the stored image and extract live EXIF; {} when impossible.

    Videos are never decrypted for a probe, JPEG XL carries no probeable
    EXIF (djxl yields bare pixels) and oversized files are skipped; the
    stored png_text_chunks/taken_at still cover those.
    """
    if media.get("media_type") != "image":
        return {}
    content_type = (media.get("content_type") or "").lower()
    if content_type == "image/jxl":
        return {}
    if (media.get("file_size") or 0) > _EXIF_PROBE_MAX_BYTES:
        return {}
    try:
        blob = await get_storage().download(item_id, folder="uploads")
        return extract_image_exif(blob)
    except Exception:
        return {}


def _decode_base64_text(value: str, label: str) -> str:
    """Decode one base64 value into clean text, with tool-visible errors."""
    compact = "".join(value.split())
    try:
        raw = base64.b64decode(compact, validate=True)
        return raw.decode("utf-8")
    # UnicodeDecodeError is a ValueError subclass: catch it first.
    except UnicodeDecodeError:
        raise ToolError(
            f"base64 value of '{label}' does not decode to UTF-8 text"
        )
    except (binascii.Error, ValueError):
        raise ToolError(
            f"value of '{label}' is not valid base64; "
            "retry without decode_base64 to read the raw stored text"
        )


def _page_text(value: str, args: dict, key_label: str,
               base64_decoded: bool = False) -> str:
    """Slice a full metadata value into one paged tool result."""
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    max_chars = _clamp_int(
        args.get("max_chars"), 1000, _METADATA_KEY_MAX_CHARS,
        _METADATA_KEY_MAX_CHARS,
    )
    total = len(value)
    chunk = value[offset:offset + max_chars]
    next_offset = offset + len(chunk)
    result = {
        "key": key_label,
        "value": chunk,
        "total_chars": total,
        "next_offset": next_offset if next_offset < total else None,
    }
    if base64_decoded:
        result["base64_decoded"] = True
    return json.dumps(result, ensure_ascii=False)


def _metadata_fetch_key(key: str, chunks: dict, args: dict,
                        decode_base64: bool = False) -> str:
    """Return the full paged value of one text chunk."""
    if key in chunks:
        value = chunks[key]
        if decode_base64:
            value = _decode_base64_text(value, key)
        return _page_text(value, args, key, base64_decoded=decode_base64)
    raise ToolError(
        f"unknown metadata key '{key}'; available: "
        + (", ".join(sorted(chunks)) if chunks else "(none)")
    )


def _summarize_exif(live: dict) -> dict:
    """Compact JSON-safe summary of a live EXIF probe result."""
    out = {
        "camera": live.get("camera"),
        "lens": live.get("lens"),
        "iso": live.get("iso"),
        "exposure_time": live.get("exposure_time"),
        "f_number": live.get("f_number"),
        "focal_length_mm": live.get("focal_length_mm"),
        "gps": live.get("gps"),
    }
    for name in ("user_comment", "image_description"):
        value = live.get(name)
        if value:
            out[name] = {
                "length": len(value),
                "preview": value[:_METADATA_PREVIEW_CHARS],
            }
    xmp = live.get("xmp")
    if xmp:
        out["xmp"] = {"length": len(xmp)}
    return out


def _compile_grep(pattern: str, case_sensitive: bool, regex: bool):
    """Compile the grep pattern; substring mode escapes it.

    Always returns a compiled ``re.Pattern`` so span extraction is uniform.
    Raises ToolError on an invalid regular expression.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    source = pattern if regex else re.escape(pattern)
    try:
        return re.compile(source, flags)
    except re.error as exc:
        raise ToolError(f"invalid regex pattern: {exc}")


def _grep_snippet(text: str, match, context_chars: int) -> str:
    """Text around a regex match, ellipsized when cut."""
    start = match.start()
    end = match.end()
    half = context_chars // 2
    ctx_start = max(0, start - half)
    ctx_end = min(len(text), end + half)
    prefix = "..." if ctx_start > 0 else ""
    suffix = "..." if ctx_end < len(text) else ""
    return f"{prefix}{text[ctx_start:ctx_end]}{suffix}"


def _grep_count(compiled, text: str) -> int:
    """Occurrence count of the pattern in text (for match_count fields)."""
    return sum(1 for _ in compiled.finditer(text))


# ============================================================================
# READ tools
# ============================================================================

def _tool_search_items(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    limit = _clamp_int(args.get("limit"), 1, 50, 20)

    db = create_connection()
    try:
        folder_id = (
            _resolve_folder_id(db, ctx.user_id, args.get("folder_id"))
            if args.get("folder_id")
            else None
        )
        result = _tag_service(db).search_items(query, folder_id, sort_by="uploaded")
    finally:
        db.close()

    items = []
    for row in result.get("items", [])[:limit]:
        items.append({
            "id": short_id(row.get("id")),
            "title": row.get("title"),
            "type": MEDIA_TYPE,
            "media_type": row.get("media_type"),
            "content_type": row.get("content_type"),
            "folder_id": short_id(row.get("folder_id")),
            "uploaded_at": str(row.get("uploaded_at")) if row.get("uploaded_at") else None,
        })
    return json.dumps({
        "items": items,
        "total": result.get("total", len(items)),
        "note": "tag search; prefix a tag with '-' to exclude",
    }, ensure_ascii=False)


async def _tool_grep_library(args: dict, ctx: ToolContext) -> str:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ToolError("pattern is required")

    requested_sources = args.get("sources")
    if requested_sources is not None:
        if not isinstance(requested_sources, list):
            raise ToolError("sources must be a list of source names")
        for src in requested_sources:
            if src not in _GREP_SOURCES:
                raise ToolError(
                    f"unknown source '{src}'; valid sources: "
                    + ", ".join(_GREP_SOURCES)
                )
        sources = requested_sources
    else:
        sources = list(_GREP_SOURCES)

    regex = bool(args.get("regex"))
    case_sensitive = bool(args.get("case_sensitive"))
    compiled = _compile_grep(pattern, case_sensitive, regex)

    context_chars = _clamp_int(
        args.get("context_chars"), 0, _GREP_CONTEXT_MAX_CHARS,
        _GREP_DEFAULT_CONTEXT_CHARS,
    )
    limit = _clamp_int(args.get("limit"), 1, 50, 20)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)

    folder_id_arg = args.get("folder_id") or None
    db = create_connection()
    try:
        folder_filter_sql = ""
        folder_filter_params: list = []
        if folder_id_arg is not None:
            folder_id_resolved = _resolve_folder_id(
                db, ctx.user_id, folder_id_arg, owner_only=True
            )
            folder_repo = FolderRepository(db)
            folder = folder_repo.get_by_id(str(folder_id_resolved))
            if folder is None or folder.get("user_id") != ctx.user_id:
                raise ToolError(f"folder not found: {folder_id_arg}")
            subtree_ids = (
                folder_repo.get_subtree_ids(folder["id"])
                if args.get("include_subfolders") is not False
                else [folder["id"]]
            )
            placeholders = ",".join("?" for _ in subtree_ids)
            folder_filter_sql = f" AND folder_id IN ({placeholders})"
            folder_filter_params = list(subtree_ids)
            folder_id_out = short_id(folder_id_resolved)
        else:
            folder_id_out = None

        # ------------------------------------------------------------------
        # Per-source match collection
        # ------------------------------------------------------------------
        results: dict[str, dict] = {}
        skipped_oversized = 0
        not_scanned = False
        decrypt_failed = 0
        has_more = False
        _dek_warnings: list[str] = []

        for source in sources:
            matches: list[dict] = []

            if source == "titles":
                rows = [
                    dict(r)
                    for r in db.execute(
                        f"SELECT id, title, folder_id FROM items "
                        f"WHERE user_id = ? AND title IS NOT NULL AND title != ''"
                        f"{folder_filter_sql}",
                        [ctx.user_id, *folder_filter_params],
                    ).fetchall()
                ]
                for row in rows:
                    text = row["title"] or ""
                    for m in compiled.finditer(text):
                        matches.append({
                            "id": short_id(row["id"]),
                            "title": row["title"],
                            "folder_id": short_id(row.get("folder_id")),
                            "snippet": _grep_snippet(text, m, context_chars),
                        })

            elif source == "descriptions":
                rows = [
                    dict(r)
                    for r in db.execute(
                        f"SELECT id, title, description, folder_id FROM items "
                        f"WHERE user_id = ? AND description IS NOT NULL "
                        f"AND description != ''{folder_filter_sql}",
                        [ctx.user_id, *folder_filter_params],
                    ).fetchall()
                ]
                for row in rows:
                    text = row["description"] or ""
                    for m in compiled.finditer(text):
                        matches.append({
                            "id": short_id(row["id"]),
                            "title": row["title"],
                            "folder_id": short_id(row.get("folder_id")),
                            "snippet": _grep_snippet(text, m, context_chars),
                        })

            elif source == "filenames":
                rows = [
                    dict(r)
                    for r in db.execute(
                        f"SELECT i.id, i.title, i.folder_id, m.original_name "
                        f"FROM items i JOIN item_media m ON m.item_id = i.id "
                        f"WHERE i.user_id = ? AND m.original_name IS NOT NULL "
                        f"AND m.original_name != ''{folder_filter_sql}",
                        [ctx.user_id, *folder_filter_params],
                    ).fetchall()
                ]
                for row in rows:
                    text = row["original_name"] or ""
                    for m in compiled.finditer(text):
                        matches.append({
                            "id": short_id(row["id"]),
                            "title": row["title"],
                            "original_name": row["original_name"],
                            "folder_id": short_id(row.get("folder_id")),
                            "snippet": _grep_snippet(text, m, context_chars),
                        })

            elif source == "tags":
                all_tags = [
                    dict(r)
                    for r in db.execute(
                        "SELECT id, name, description FROM tags"
                    ).fetchall()
                ]
                for tag in all_tags:
                    for field in ("name", "description"):
                        text = tag.get(field) or ""
                        for m in compiled.finditer(text):
                            entry: dict = {
                                "id": str(tag["id"]),
                                "name": tag["name"],
                                "field": field,
                            }
                            if field == "description":
                                entry["snippet"] = _grep_snippet(
                                    text, m, context_chars
                                )
                            matches.append(entry)

            elif source == "metadata":
                rows = [
                    dict(r)
                    for r in db.execute(
                        f"SELECT i.id, i.title, i.folder_id, m.png_text_chunks "
                        f"FROM items i JOIN item_media m ON m.item_id = i.id "
                        f"WHERE i.user_id = ? AND m.png_text_chunks IS NOT NULL "
                        f"AND m.png_text_chunks NOT IN ('', '{{}}')"
                        f"{folder_filter_sql}",
                        [ctx.user_id, *folder_filter_params],
                    ).fetchall()
                ]
                for row in rows:
                    chunks_str = row["png_text_chunks"] or "{}"
                    try:
                        chunks = json.loads(chunks_str)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(chunks, dict):
                        continue
                    for chunk_key, chunk_val in chunks.items():
                        val_str = str(chunk_val) if chunk_val is not None else ""
                        key_match = compiled.search(str(chunk_key))
                        val_match = compiled.search(val_str)
                        if key_match or val_match:
                            entry = {
                                "id": short_id(row["id"]),
                                "title": row["title"],
                                "chunk": chunk_key,
                            }
                            if val_match:
                                entry["snippet"] = _grep_snippet(
                                    val_str, val_match, context_chars
                                )
                            matches.append(entry)

            elif source == "notes":
                if ctx.dek is None:
                    _dek_warnings.append(
                        "notes source skipped: encryption key not available"
                    )
                    matches = []
                else:
                    note_rows = [
                        dict(r)
                        for r in db.execute(
                            f"SELECT i.id, i.title, i.folder_id, t.char_count "
                            f"FROM items i JOIN item_texts t ON t.item_id = i.id "
                            f"WHERE i.user_id = ? AND i.type = 'note'"
                            f"{folder_filter_sql} ORDER BY i.uploaded_at DESC",
                            [ctx.user_id, *folder_filter_params],
                        ).fetchall()
                    ]

                    eligible_count = 0
                    for row in note_rows:
                        # char_count approximates file size for UTF-8 text.
                        file_size = (row.get("char_count") or 0) * 4
                        if file_size > _GREP_NOTE_MAX_BYTES:
                            skipped_oversized += 1
                            continue
                        eligible_count += 1
                        if eligible_count > _GREP_MAX_NOTES:
                            not_scanned = True
                            break

                        item_id = row["id"]
                        title = row["title"]
                        try:
                            note_text = await _read_note_text(item_id, ctx.dek)
                        except Exception:
                            decrypt_failed += 1
                            continue

                        note_matches = list(compiled.finditer(note_text))
                        if not note_matches:
                            continue

                        mc = _grep_count(compiled, note_text)
                        first_m = note_matches[0]
                        matches.append({
                            "id": short_id(item_id),
                            "title": title,
                            "folder_id": short_id(row.get("folder_id")),
                            "match_count": mc,
                            "snippet": _grep_snippet(note_text, first_m, context_chars),
                        })

            # Pagination per source
            page = matches[offset:offset + limit]
            results[source] = {"total": len(matches), "items": page}

            if offset + limit < len(matches):
                has_more = True

    finally:
        db.close()

    # Build warnings list from accumulated counts
    notes_list = []
    if skipped_oversized > 0:
        notes_list.append(
            f"{skipped_oversized} note(s) skipped (too large)"
        )
    if not_scanned:
        notes_list.append(
            f"more eligible notes exist but scanning was capped at "
            f"{_GREP_MAX_NOTES}"
        )
    if decrypt_failed > 0:
        notes_list.append(f"{decrypt_failed} note(s) failed to decrypt")
    notes_list.extend(_dek_warnings)

    result = {
        "pattern": pattern,
        "regex": regex,
        "case_sensitive": case_sensitive,
        "folder_id": folder_id_out,
        "results": results,
    }
    if has_more:
        result["next_offset"] = offset + limit
        result["note"] = "page through more matches with next_offset"
    if notes_list:
        result["notes"] = notes_list

    return json.dumps(result, ensure_ascii=False)


# Albums shown in one list_folder result; the rest is reachable via
# list_albums (paged). Keeps the tool result under the orchestrator's
# TOOL_RESULT_LIMIT so the JSON is not cut mid-array.
_ALBUMS_PER_FOLDER_LIST = 50


def _tool_list_folder(args: dict, ctx: ToolContext) -> str:
    folder_id = args.get("folder_id") or None
    # Large pages are safe now that ids are short: with the default
    # TOOL_RESULT_LIMIT (16k) several hundred compact entries fit.
    limit = _clamp_int(args.get("limit"), 1, 1000, 200)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)

    db = create_connection()
    try:
        service = _folder_service(db)
        if folder_id is not None:
            folder_id = _resolve_folder_id(db, ctx.user_id, folder_id)
        if folder_id is None:
            # Virtual library root: every top-level folder with its albums
            # and standalone items — the same picture the gallery sidebar
            # shows. A library has many root folders, so listing one of
            # them would hide the rest.
            subfolders, albums, items = [], [], []
            for node in service.get_folder_tree(ctx.user_id):
                if node.get("parent_id") is not None:
                    continue
                try:
                    contents = service.get_folder_contents(node["id"], ctx.user_id)
                except HTTPException:
                    continue  # no access to that top-level folder
                subfolders.append({
                    "id": node["id"],
                    "name": node["name"],
                    "item_count": node.get("item_count", 0),
                    "album_count": len(contents.get("albums", [])),
                })
                albums.extend(contents.get("albums", []))
                items.extend(contents.get("items", []))
            folder_info: dict = {"root": True}
        else:
            try:
                contents = service.get_folder_contents(folder_id, ctx.user_id)
            except HTTPException as exc:
                raise _http_to_tool_error(exc)
            subfolders = [
                {"id": f["id"], "name": f["name"]}
                for f in contents.get("subfolders", [])
            ]
            albums = contents.get("albums", [])
            items = contents.get("items", [])
            folder_row = FolderRepository(db).get_by_id(str(folder_id))
            folder_info = {
                "id": folder_id,
                "name": folder_row.get("name") if folder_row else None,
            }
    finally:
        db.close()

    total_items = len(items)
    page = items[offset:offset + limit]
    result = {
        "folder": (
            folder_info
            if folder_info.get("root")
            else {**folder_info, "id": short_id(folder_info.get("id"))}
        ),
        "subfolders": [
            {**s, "id": short_id(s["id"])} for s in subfolders
        ],
        "albums": [
            {
                "id": short_id(a["id"]),
                "name": a["name"],
                "folder_id": short_id(a.get("folder_id")),
                "item_count": a.get("item_count", 0),
            }
            for a in albums[:_ALBUMS_PER_FOLDER_LIST]
        ],
        # Standalone items only (album members live in their albums);
        # items are always media, so no per-item "type" field.
        "items": [
            {
                "id": short_id(i["id"]),
                "title": i.get("title"),
                "media_type": i.get("media_type"),
            }
            for i in page
        ],
        "total": total_items,
        "offset": offset,
    }
    if offset + len(page) < total_items:
        result["next_offset"] = offset + len(page)
    if len(albums) > _ALBUMS_PER_FOLDER_LIST:
        result["albums_truncated"] = True
    if "next_offset" in result or "albums_truncated" in result:
        notes = []
        if "next_offset" in result:
            notes.append("page through more items with the next_offset")
        if "albums_truncated" in result:
            notes.append(
                f"{len(albums)} albums in total; page through all of them "
                "with list_albums"
            )
        result["note"] = "; ".join(notes)
    return json.dumps(result, ensure_ascii=False)


def _tool_get_item(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")

    db = create_connection()
    try:
        item_id = _get_owned_item(db, item_id, ctx)["id"]
        item = _item_service(db).get_item(item_id)
        tags = _tag_service(db).get_item_tags(item_id)
        media_detail = (
            ItemMediaRepository(db).get_by_item_id(item_id)
            if item.get("type") == MEDIA_TYPE
            else None
        )
    finally:
        db.close()

    result = {
        "id": short_id(item["id"]),
        "title": item.get("title"),
        "description": item.get("description"),
        "type": item.get("type"),
        "folder_id": short_id(item.get("folder_id")),
        "uploaded_at": str(item.get("uploaded_at")) if item.get("uploaded_at") else None,
        "media_type": item.get("media_type"),
        "content_type": item.get("content_type"),
        "width": item.get("width"),
        "height": item.get("height"),
        "duration": item.get("duration"),
        "taken_at": str(item.get("taken_at")) if item.get("taken_at") else None,
        "tags": {
            "explicit": [t["name"] for t in tags.get("explicit_tags", [])],
            "implied": [t["name"] for t in tags.get("implied_tags", [])],
        },
    }
    if media_detail is not None:
        chunks = media_detail.get("png_text_chunks") or {}
        result["file_size"] = media_detail.get("file_size")
        result["original_name"] = media_detail.get("original_name")
        # Names only: full values can be huge (ComfyUI workflows) and are
        # fetched on demand via get_item_metadata.
        result["text_chunks"] = sorted(chunks)
    if item.get("type") == NOTE_TYPE:
        result["char_count"] = item.get("char_count")
        result["line_count"] = item.get("line_count")
    return json.dumps(result, ensure_ascii=False)


async def _tool_get_item_metadata(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")

    db = create_connection()
    try:
        item = _get_owned_item(db, item_id, ctx)
        item_id = item["id"]
        item_type = item.get("type")
        if item_type == NOTE_TYPE:
            text = ItemTextRepository(db).get_by_item_id(item_id)
            return json.dumps({
                "type": NOTE_TYPE,
                "title": item.get("title"),
                "content_type": text.get("content_type") if text else None,
                "encoding": text.get("encoding") if text else None,
                "char_count": text.get("char_count") if text else None,
                "line_count": text.get("line_count") if text else None,
                "note": "text note has no embedded file metadata; "
                        "use read_note for its content",
            }, ensure_ascii=False)
        if item_type != MEDIA_TYPE or not is_known_item_type(item_type):
            raise ToolError(f"unsupported item type: {item_type}")
        media = ItemMediaRepository(db).get_by_item_id(item_id)
    finally:
        db.close()
    if media is None:
        raise ToolError(f"media detail not found for item {item_id}")

    chunks = media.get("png_text_chunks") or {}
    key = str(args.get("key") or "").strip()
    decode_base64 = bool(args.get("decode_base64"))
    if key:
        if key in chunks:
            return _metadata_fetch_key(key, chunks, args, decode_base64)
        if key in ("user_comment", "image_description", "xmp"):
            live = await _probe_exif(item_id, media)
            value = live.get(key)
            if not value:
                raise ToolError(f"item {item_id} has no {key}")
            if decode_base64:
                value = _decode_base64_text(value, key)
            return _page_text(value, args, key,
                              base64_decoded=decode_base64)
        raise ToolError(
            f"unknown metadata key '{key}'; available: "
            + (
                ", ".join(sorted(chunks))
                if chunks
                else "(none) or user_comment / image_description / xmp"
            )
        )

    summary = {
        "id": short_id(item_id),
        "type": MEDIA_TYPE,
        "media_type": media.get("media_type"),
        "content_type": media.get("content_type"),
        "original_name": media.get("original_name"),
        "width": media.get("width"),
        "height": media.get("height"),
        "duration": media.get("duration"),
        "file_size": media.get("file_size"),
        "taken_at": str(media["taken_at"]) if media.get("taken_at") else None,
    }
    chunk_entries = [
        {
            "key": name,
            "length": len(value),
            "preview": value[:_METADATA_PREVIEW_CHARS],
        }
        for name, value in sorted(chunks.items())
    ]
    summary["text_chunks"] = {
        "total_keys": len(chunk_entries),
        "keys": chunk_entries[:_METADATA_MAX_KEYS],
        "note": (
            "read a full value with key='<name>'"
            if chunk_entries
            else None
        ),
    }
    if media.get("media_type") == "video":
        summary["exif"] = None
        summary["probe_note"] = (
            "live EXIF probe is image-only; videos rely on stored fields"
        )
    else:
        live = await _probe_exif(item_id, media)
        if live:
            summary["exif"] = _summarize_exif(live)
            summary["probe_note"] = (
                "fetch full user_comment / image_description / xmp "
                "with key='...'"
                if any(live.get(k) for k in
                       ("user_comment", "image_description", "xmp"))
                else None
            )
        else:
            summary["exif"] = None
            if (media.get("content_type") or "").lower() == "image/jxl":
                summary["probe_note"] = (
                    "JPEG XL carries no probeable EXIF; text_chunks and "
                    "taken_at come from the database"
                )
            else:
                summary["probe_note"] = "no EXIF found"
    return json.dumps(summary, ensure_ascii=False)


def _tool_set_item_metadata(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")
    chunks_arg = args.get("chunks")
    if not isinstance(chunks_arg, dict) or not chunks_arg:
        raise ToolError("chunks must be a non-empty object of key -> text")
    mode = str(args.get("mode") or "merge").strip().lower()
    if mode not in ("merge", "replace_all"):
        raise ToolError("mode must be 'merge' or 'replace_all'")
    encode_base64 = bool(args.get("encode_base64"))

    updates = {}
    for raw_key, raw_value in chunks_arg.items():
        key = str(raw_key).strip()
        if not key:
            raise ToolError("metadata keys must be non-empty")
        if len(key) > _METADATA_SET_KEY_MAX_CHARS:
            raise ToolError(
                f"metadata key must be at most "
                f"{_METADATA_SET_KEY_MAX_CHARS} chars: '{key[:50]}...'"
            )
        if not isinstance(raw_value, str):
            raise ToolError(f"metadata value of '{key}' must be a string")
        if encode_base64:
            updates[key] = base64.b64encode(
                raw_value.encode("utf-8")
            ).decode("ascii")
        else:
            updates[key] = raw_value

    db = create_connection()
    try:
        item = _get_owned_item(db, item_id, ctx)
        item_id = item["id"]
        if item.get("type") != MEDIA_TYPE or not is_known_item_type(item.get("type")):
            raise ToolError(
                f"item {item_id} is not a media item "
                f"(type: {item.get('type')}); metadata blocks are "
                "media-only"
            )
        media = ItemMediaRepository(db).get_by_item_id(item_id)
        if media is None:
            raise ToolError(f"media detail not found for item {item_id}")
        existing = media.get("png_text_chunks") or {}
        merged = existing if mode == "merge" else {}
        merged = {**merged, **updates}
        if sum(len(k) + len(v) for k, v in merged.items()) > \
                _METADATA_SET_TOTAL_MAX_CHARS:
            raise ToolError(
                "combined metadata blocks exceed the "
                f"{_METADATA_SET_TOTAL_MAX_CHARS} chars size limit"
            )
        ItemMediaRepository(db).update(item_id, png_text_chunks=merged)
        ItemRepository(db).touch_updated_at(item_id)
    finally:
        db.close()

    result = {
        "id": short_id(item_id),
        "updated_keys": sorted(updates),
        "text_chunks": sorted(merged),
        "base64_encoded": encode_base64,
    }
    removed = sorted(set(existing) - set(merged))
    if removed:
        result["removed_keys"] = removed
    return json.dumps(result, ensure_ascii=False)


async def _tool_read_note(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = _get_owned_item(db, item_id, ctx)
        item_id = item["id"]
        if item.get("type") != NOTE_TYPE:
            raise ToolError(
                f"item {item_id} is not a text note "
                f"(type: {item.get('type')}); only notes have readable text"
            )
        text = ItemTextRepository(db).get_by_item_id(item_id)
    finally:
        db.close()

    content = await _read_note_text(item_id, dek)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    max_chars = _clamp_int(
        args.get("max_chars"), 500, _NOTE_READ_MAX_CHARS,
        _NOTE_READ_DEFAULT_CHARS,
    )
    total = len(content)
    chunk = content[offset:offset + max_chars]
    next_offset = offset + len(chunk)
    return json.dumps({
        "id": short_id(item_id),
        "title": item.get("title"),
        "content_type": text.get("content_type") if text else None,
        "total_chars": total,
        "offset": offset,
        "content": chunk,
        "next_offset": next_offset if next_offset < total else None,
    }, ensure_ascii=False)


def _tool_list_tags(args: dict, ctx: ToolContext) -> str:
    query = args.get("query") or None
    limit = _clamp_int(args.get("limit"), 1, 100, 50)
    category_slug = str(args.get("category") or "").strip().lower() or None

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        category_id = None
        if category_slug:
            category = tags_repo.get_category_by_slug(category_slug)
            if category is None:
                matches = [
                    c for c in tags_repo.get_categories()
                    if c["name"].lower() == category_slug
                ]
                category = matches[0] if matches else None
            if category is None:
                raise ToolError(f"tag category '{category_slug}' not found")
            category_id = category["id"]
        items = tags_repo.list_tags(query=query, limit=limit, category_id=category_id)
        total = tags_repo.count_tags(query=query, category_id=category_id)
    finally:
        db.close()

    tags = []
    for t in items:
        entry = {
            "id": t["id"],
            "name": t["name"],
            "category": t.get("category_name"),
            "usage_count": t.get("usage_count", 0),
        }
        description = (t.get("description") or "").strip()
        if description:
            entry["description"] = description
        tags.append(entry)

    return json.dumps({"tags": tags, "total": total}, ensure_ascii=False)


def _tool_get_tag_info(args: dict, ctx: ToolContext) -> str:
    raw_names = args.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        raise ToolError("names must be a non-empty array of tag names")
    if len(raw_names) > _TAG_INFO_MAX_NAMES:
        raise ToolError(
            f"too many names: {len(raw_names)}; split into batches of at "
            f"most {_TAG_INFO_MAX_NAMES}"
        )
    include_implications = args.get("include_implications") is not False

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        resolved = _tag_service(db).resolve_tags([str(n) for n in raw_names])
        found_entries = [e for e in resolved if e["tag"] is not None]
        invalid = [e["input"] for e in resolved if not e["valid"]]
        not_found = sorted({
            e["name"] for e in resolved if e["valid"] and e["tag"] is None
        })

        implies_map: dict = {}
        implied_by_map: dict = {}
        name_by_id: dict = {}
        if include_implications and found_entries:
            impl_repo = TagImplicationRepository(db)
            ids = [e["tag"]["id"] for e in found_entries]
            implies_map = impl_repo.get_direct_implications(ids)
            implied_by_map = impl_repo.get_implied_by_many(ids)
            implied_ids = {
                tid
                for mapping in (implies_map, implied_by_map)
                for ids_in in mapping.values()
                for tid in ids_in
            }
            if implied_ids:
                name_by_id = {
                    t["id"]: t["name"]
                    for t in tags_repo.get_tags_by_ids(list(implied_ids))
                }
    finally:
        db.close()

    tags = []
    seen_ids = set()
    for e in found_entries:
        tag = e["tag"]
        if tag["id"] in seen_ids:
            continue
        seen_ids.add(tag["id"])
        entry = {
            "id": tag["id"],
            "name": tag["name"],
            "category": tag.get("category_name"),
            "usage_count": tag.get("usage_count", 0),
        }
        description = (tag.get("description") or "").strip()
        if description:
            entry["description"] = description
        if include_implications:
            implies = [
                name_by_id.get(tid, str(tid))
                for tid in implies_map.get(tag["id"], [])
            ]
            implied_by = [
                name_by_id.get(tid, str(tid))
                for tid in implied_by_map.get(tag["id"], [])
            ]
            if implies:
                entry["implies"] = implies
            if implied_by:
                entry["implied_by"] = implied_by
        tags.append(entry)

    return json.dumps({
        "tags": tags,
        "not_found": not_found,
        "invalid": invalid,
    }, ensure_ascii=False)


def _tool_list_folders(args: dict, ctx: ToolContext) -> str:
    db = create_connection()
    try:
        tree = _folder_service(db).get_folder_tree(ctx.user_id)
    finally:
        db.close()

    return json.dumps({
        "folders": [
            {
                "id": short_id(f["id"]),
                "name": f["name"],
                "parent_id": short_id(f.get("parent_id")),
            }
            for f in tree
            if f.get("user_id") == ctx.user_id
        ]
    }, ensure_ascii=False)


def _tool_list_albums(args: dict, ctx: ToolContext) -> str:
    folder_id = args.get("folder_id") or None
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    limit = _clamp_int(args.get("limit"), 1, 200, 50)

    db = create_connection()
    try:
        folder_id = (
            _resolve_folder_id(db, ctx.user_id, folder_id)
            if folder_id is not None
            else None
        )
        album_service = _album_service(db)
        if folder_id is not None:
            folder = FolderRepository(db).get_by_id(str(folder_id))
            if folder is None or (
                folder.get("user_id") != ctx.user_id
                and PermissionRepository(db).get_permission(
                    folder_id, ctx.user_id
                ) not in ("viewer", "editor")
            ):
                raise ToolError(f"folder not found: {folder_id}")
            albums = album_service.get_albums_by_folder(folder_id, ctx.user_id)
        else:
            albums = album_service.get_albums_for_user(ctx.user_id)
    finally:
        db.close()

    total = len(albums)
    page = albums[offset:offset + limit]
    result = {
        "albums": [
            {
                "id": short_id(a["id"]),
                "name": a["name"],
                "folder_id": short_id(a.get("folder_id")),
                "item_count": a.get("item_count", 0),
            }
            for a in page
        ],
        "total": total,
        "offset": offset,
    }
    if offset + len(page) < total:
        result["next_offset"] = offset + len(page)
        result["note"] = "page through more albums with the next_offset"
    return json.dumps(result, ensure_ascii=False)


def _tool_list_album_items(args: dict, ctx: ToolContext) -> str:
    album_id = str(args.get("album_id") or "").strip()
    if not album_id:
        raise ToolError("album_id is required")
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    limit = _clamp_int(args.get("limit"), 1, 200, 50)

    db = create_connection()
    try:
        album_repo = AlbumRepository(db)
        album_id = _resolve_album_id(db, ctx.user_id, album_id)
        album = album_repo.get_by_id(album_id)
        if album is None:
            raise ToolError(f"album not found: {album_id}")
        # Same visibility rule as list_albums: owner of the album's folder
        # or an explicit viewer/editor permission on it.
        folder_id = album.get("folder_id")
        folder = FolderRepository(db).get_by_id(str(folder_id))
        if folder is None or (
            folder.get("user_id") != ctx.user_id
            and PermissionRepository(db).get_permission(
                folder_id, ctx.user_id
            ) not in ("viewer", "editor")
        ):
            raise ToolError(f"album not found: {album_id}")
        items = album_repo.get_items(album_id)
    finally:
        db.close()

    total = len(items)
    page = items[offset:offset + limit]
    result = {
        "album": {
            "id": short_id(album["id"]),
            "name": album["name"],
            "folder_id": short_id(album.get("folder_id")),
            "item_count": album.get("item_count", total),
        },
        "total": total,
        "offset": offset,
        "items": [
            {
                "id": short_id(i["id"]),
                "title": i.get("title"),
                "media_type": i.get("media_type"),
                "position": i.get("position"),
            }
            for i in page
        ],
    }
    if offset + len(page) < total:
        result["next_offset"] = offset + len(page)
        result["note"] = "page through more items with the next_offset"
    return json.dumps(result, ensure_ascii=False)


def _tool_get_tag_suggestions(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")

    try:
        db = create_connection()
        try:
            item_id = _get_owned_item(db, item_id, ctx)["id"]
            service = TagSuggestionService(
                cooccurrence_repo=TagCooccurrenceRepository(db),
                tags_repo=TagsRepository(db),
                mutex_repo=TagMutexRepository(db),
                feedback_repo=TagFeedbackRepository(db),
            )
            suggestions = service.get_suggestions_for_item(item_id, limit=8)
        finally:
            db.close()
    except Exception:
        # Suggestions are best-effort: missing stats or DB hiccups must not
        # break the conversation.
        return json.dumps({"suggestions": []})

    return json.dumps({
        "suggestions": [
            {
                "id": s["id"],
                "name": s["name"],
                "suggestion_score": s.get("suggestion_score"),
            }
            for s in (suggestions or [])
        ]
    }, ensure_ascii=False)


_FEEDBACK_OUTCOMES = ("accepted", "rejected")


def _tool_record_tag_feedback(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    decisions = args.get("decisions")
    if not item_id:
        raise ToolError("item_id is required")
    if not isinstance(decisions, list) or not (1 <= len(decisions) <= 50):
        raise ToolError(
            "decisions must be a list of 1-50 {tag_id, outcome} objects"
        )

    # Last decision per tag wins; keep first-seen order for the summary.
    by_tag: dict[int, str] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ToolError(
                "each decision must be an object with tag_id and outcome"
            )
        tag_id = decision.get("tag_id")
        outcome = decision.get("outcome")
        if isinstance(tag_id, bool) or not isinstance(tag_id, int):
            raise ToolError("decision.tag_id must be an integer")
        if outcome not in _FEEDBACK_OUTCOMES:
            raise ToolError(
                "decision.outcome must be one of: "
                + ", ".join(_FEEDBACK_OUTCOMES)
            )
        by_tag[tag_id] = outcome

    db = create_connection()
    try:
        item_id = _get_owned_item(db, item_id, ctx)["id"]
        tags_repo = TagsRepository(db)
        unknown = [
            tag_id for tag_id in by_tag
            if tags_repo.get_by_id(tag_id) is None
        ]
        if unknown:
            raise ToolError(
                "unknown tag ids: " + ", ".join(str(t) for t in unknown)
            )
        context_ids = [t["id"] for t in tags_repo.get_item_tags_all(item_id)]
        service = TagSuggestionService(
            cooccurrence_repo=TagCooccurrenceRepository(db),
            tags_repo=tags_repo,
            mutex_repo=TagMutexRepository(db),
            feedback_repo=TagFeedbackRepository(db),
        )
        recorded = []
        for tag_id, outcome in by_tag.items():
            service.record_feedback(item_id, context_ids, tag_id, outcome)
            recorded.append({
                "tag": tags_repo.get_by_id(tag_id)["name"],
                "outcome": outcome,
            })
    finally:
        db.close()

    notes = []
    if any(r["outcome"] == "accepted" for r in recorded):
        notes.append(
            "accepted does not add the tag; use add_item_tags to apply it"
        )
    if any(r["outcome"] == "rejected" for r in recorded):
        notes.append(
            "rejected tags no longer appear in suggestions for this item"
        )
    return json.dumps(
        {"recorded": recorded, "note": "; ".join(notes)},
        ensure_ascii=False,
    )


def _tool_audit_library(args: dict, ctx: ToolContext) -> str:
    requested = args.get("checks")
    if requested is None:
        checks = list(AUDIT_CHECKS)
    else:
        if not isinstance(requested, list) or not requested:
            raise ToolError("checks must be a non-empty list of check ids")
        checks = []
        for raw in requested:
            name = str(raw)
            if name not in AUDIT_CHECKS:
                raise ToolError(
                    f"unknown check '{name}'; valid checks: "
                    + ", ".join(AUDIT_CHECKS)
                )
            if name not in checks:
                checks.append(name)

    folder_id = args.get("folder_id") or None
    include_subfolders = args.get("include_subfolders") is not False
    limit = _clamp_int(args.get("limit"), 1, 100, 50)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)

    db = create_connection()
    try:
        folder_ids = None
        if folder_id is not None:
            folder_repo = FolderRepository(db)
            folder_id = _resolve_folder_id(
                db, ctx.user_id, folder_id, owner_only=True
            )
            folder = folder_repo.get_by_id(str(folder_id))
            if folder is None or folder.get("user_id") != ctx.user_id:
                raise ToolError(f"folder not found: {folder_id}")
            folder_ids = (
                folder_repo.get_subtree_ids(folder["id"])
                if include_subfolders
                else [folder["id"]]
            )
        rows = ItemRepository(db).get_audit_problems(
            ctx.user_id, checks, folder_ids=folder_ids
        )
    finally:
        db.close()

    summary = {name: 0 for name in checks}
    for row in rows:
        for name in checks:
            if row[name]:
                summary[name] += 1

    result = {
        "checks": checks,
        "check_meanings": {
            name: AUDIT_CHECKS[name].description for name in checks
        },
        "summary": summary,
        "total": len(rows),
        "offset": offset,
        "items": [
            {
                "id": short_id(row["id"]),
                "title": row["title"],
                "type": row["type"],
                "media_type": row.get("media_type"),
                "folder_id": short_id(row["folder_id"]),
                "uploaded_at": (
                    str(row["uploaded_at"]) if row["uploaded_at"] else None
                ),
                "problems": [name for name in checks if row[name]],
            }
            for row in rows[offset:offset + limit]
        ],
        "truncated": offset + limit < len(rows),
    }
    if folder_id is not None:
        result["folder_id"] = short_id(folder_id)
    if result["truncated"]:
        result["note"] = "page through more items with a larger offset"
    return json.dumps(result, ensure_ascii=False)


# ============================================================================
# WRITE tools
# ============================================================================

def _tool_add_item_tags(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    tag_names = args.get("tag_names") or []
    if not item_id:
        raise ToolError("item_id is required")
    if not isinstance(tag_names, list) or not (1 <= len(tag_names) <= 100):
        raise ToolError("tag_names must be a list of 1-100 tag names")

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        tag_service = _tag_service(db)
        item_id = _get_owned_item(db, item_id, ctx)["id"]
        unique, found = _resolve_unknown_tags(tags_repo, tag_names)
        if unique:
            unknown = [n for n in unique if not found.get(n)]
            if unknown:
                raise ToolError(
                    "unknown tags: " + ", ".join(unknown)
                    + ". Report missing tags to the user instead of "
                    "creating them."
                )
        added = []
        try:
            for name in unique:
                tag = found[name][0]
                tag_service.add_tag_to_item(item_id, tag["id"])
                added.append(tag["name"])
            tags = tag_service.get_item_tags(item_id)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({
        "added": added,
        "tags": [t["name"] for t in tags.get("explicit_tags", [])],
        "implied_tags": [t["name"] for t in tags.get("implied_tags", [])],
    }, ensure_ascii=False)


def _tool_remove_item_tags(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    tag_names = args.get("tag_names") or []
    if not item_id:
        raise ToolError("item_id is required")
    if not isinstance(tag_names, list) or not (1 <= len(tag_names) <= 30):
        raise ToolError("tag_names must be a list of 1-30 tag names")

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        tag_service = _tag_service(db)
        item_id = _get_owned_item(db, item_id, ctx)["id"]
        unique, found = _resolve_unknown_tags(tags_repo, tag_names)
        if unique:
            unknown = [n for n in unique if not found.get(n)]
            if unknown:
                raise ToolError(
                    "unknown tags: " + ", ".join(unknown)
                    + ". Only existing tags can be removed."
                )
        removed = []
        try:
            for name in unique:
                tag = found[name][0]
                tag_service.remove_tag_from_item(item_id, tag["id"])
                removed.append(tag["name"])
            tags = tag_service.get_item_tags(item_id)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({
        "removed": removed,
        "tags": [t["name"] for t in tags.get("explicit_tags", [])],
    }, ensure_ascii=False)


def _tool_create_tags(args: dict, ctx: ToolContext) -> str:
    tag_names = args.get("tag_names") or []
    category_id = args.get("category_id")
    if not isinstance(tag_names, list) or not (1 <= len(tag_names) <= 20):
        raise ToolError("tag_names must be a list of 1-20 tag names")

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        tag_service = _tag_service(db)
        try:
            entries = tag_service.resolve_tags(tag_names, create_missing=False)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)

        invalid = [e["input"] for e in entries if not e["valid"]]
        existing = list(dict.fromkeys(
            e["name"] for e in entries if e["valid"] and e["tag"]
        ))
        # resolve_tags keeps one entry per input; collapse repeated names so
        # a duplicate is not reported as created twice.
        missing = list(dict.fromkeys(
            e["name"] for e in entries if e["valid"] and not e["tag"]
        ))

        created: list[str] = []
        try:
            if missing and category_id is None:
                # resolve_tags creates valid missing names in 'general'.
                created_entries = tag_service.resolve_tags(
                    missing, create_missing=True
                )
                created = [e["name"] for e in created_entries if e["created"]]
            elif missing:
                cat_id = _resolve_category_id(tags_repo, category_id)
                for name in missing:
                    tags_repo.create(name, cat_id, "")
                    created.append(name)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({
        "created": created,
        "existing": existing,
        "invalid": invalid,
    }, ensure_ascii=False)


def _tool_set_tag_descriptions(args: dict, ctx: ToolContext) -> str:
    raw_updates = args.get("updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        raise ToolError(
            "updates must be a non-empty array of {name, description} objects"
        )
    if len(raw_updates) > _TAG_DESC_MAX_UPDATES:
        raise ToolError(
            f"too many updates: {len(raw_updates)}; split into batches of at "
            f"most {_TAG_DESC_MAX_UPDATES}"
        )

    # Validate and dedupe (by normalized name, last write wins) before
    # touching the database so a bad batch never writes anything.
    descriptions: dict[str, str] = {}
    order: list[str] = []
    for i, raw in enumerate(raw_updates):
        if not isinstance(raw, dict):
            raise ToolError(
                f"updates[{i}] must be an object with name and description"
            )
        name = _normalize_tag_name(raw.get("name"))
        description = raw.get("description")
        if not name:
            raise ToolError(f"updates[{i}].name is required")
        if not isinstance(description, str):
            raise ToolError(
                f"updates[{i}].description must be a string "
                "(empty string clears the description)"
            )
        if "\x00" in description:
            raise ToolError(
                f"updates[{i}].description must not contain NUL bytes"
            )
        if len(description) > TAG_DESCRIPTION_MAX_CHARS:
            raise ToolError(
                f"updates[{i}].description is too long "
                f"({len(description)} chars; max {TAG_DESCRIPTION_MAX_CHARS})"
            )
        if name not in descriptions:
            order.append(name)
        descriptions[name] = description

    db = create_connection()
    try:
        try:
            entries = _tag_service(db).resolve_tags(order, create_missing=False)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)

        invalid = sorted({e["input"] for e in entries if not e["valid"]})
        not_found = sorted({
            e["name"] for e in entries if e["valid"] and e["tag"] is None
        })
        if invalid or not_found:
            # All-or-nothing: report everything that is wrong in one go.
            parts = []
            if invalid:
                parts.append("invalid names: " + ", ".join(invalid))
            if not_found:
                parts.append("unknown tags: " + ", ".join(not_found))
            raise ToolError("; ".join(parts))

        tags_repo = TagsRepository(db)
        updated = []
        for e in entries:
            tag = e["tag"]
            tags_repo.update_tag(tag["id"], description=descriptions[e["name"]])
            updated.append({
                "id": tag["id"],
                "name": tag["name"],
                "chars": len(descriptions[e["name"]]),
            })
    finally:
        db.close()

    return json.dumps({"updated": updated}, ensure_ascii=False)


def _tool_update_item(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    title = args.get("title")
    description = args.get("description")
    if not item_id:
        raise ToolError("item_id is required")
    if title is None and description is None:
        raise ToolError("provide title and/or description")

    db = create_connection()
    try:
        item_repo = ItemRepository(db)
        item_id = _get_owned_item(db, item_id, ctx)["id"]
        # update_metadata writes title/description and bumps updated_at.
        item_repo.update_metadata(item_id, title=title, description=description)
        updated = item_repo.get_by_id(item_id)
    finally:
        db.close()

    return json.dumps({
        "id": short_id(item_id),
        "title": updated.get("title") if updated else title,
        "description": updated.get("description") if updated else description,
    }, ensure_ascii=False)


async def _tool_create_note(args: dict, ctx: ToolContext) -> str:
    folder_id = str(args.get("folder_id") or "").strip()
    title = str(args.get("title") or "").strip()
    if not folder_id:
        raise ToolError("folder_id is required")
    if not title:
        raise ToolError("title is required")
    content, line_count = _validate_note_content(args.get("content"))
    content_type = str(args.get("content_type") or "text/plain").strip().lower()
    if content_type not in NOTE_CONTENT_TYPES:
        raise ToolError(
            "content_type must be one of: " + ", ".join(NOTE_CONTENT_TYPES)
        )
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        folder_id = _resolve_folder_id(
            db, ctx.user_id, folder_id, owner_only=True
        )
        folder = FolderRepository(db).get_by_id(folder_id)
        if folder is None or folder.get("user_id") != ctx.user_id:
            raise ToolError(f"folder not found: {folder_id}")
        item_id = str(uuid.uuid4())
        ItemRepository(db).create(
            item_type=NOTE_TYPE,
            folder_id=folder_id,
            user_id=ctx.user_id,
            item_id=item_id,
            title=title,
        )
        ItemTextRepository(db).create(
            item_id=item_id,
            content_type=content_type,
            original_name=title,
            char_count=len(content),
            line_count=line_count,
        )
    finally:
        db.close()

    await _write_note_blob(item_id, content, dek)

    return json.dumps({
        "id": short_id(item_id),
        "title": title,
        "folder_id": short_id(folder_id),
        "content_type": content_type,
        "char_count": len(content),
        "line_count": line_count,
    }, ensure_ascii=False)


async def _tool_write_note(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")
    mode = str(args.get("mode") or "replace").strip().lower()
    if mode not in ("replace", "append"):
        raise ToolError("mode must be 'replace' or 'append'")
    content, _ = _validate_note_content(args.get("content"))
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = _get_owned_item(db, item_id, ctx)
        item_id = item["id"]
        if item.get("type") != NOTE_TYPE:
            raise ToolError(
                f"item {item_id} is not a text note "
                f"(type: {item.get('type')})"
            )
    finally:
        db.close()

    if mode == "append":
        content = await _read_note_text(item_id, dek) + content
    line_count = _count_lines(content)

    await _write_note_blob(item_id, content, dek)

    db = create_connection()
    try:
        ItemTextRepository(db).update_stats(item_id, len(content), line_count)
        ItemRepository(db).touch_updated_at(item_id)
    finally:
        db.close()

    # Minimal ack: echoing the content back would only burn tokens.
    return json.dumps({
        "status": "ok",
        "mode": mode,
        "id": short_id(item_id),
        "char_count": len(content),
        "line_count": line_count,
    }, ensure_ascii=False)


async def _tool_edit_note(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not isinstance(old_string, str) or not old_string:
        raise ToolError("old_string must be a non-empty string")
    if not isinstance(new_string, str):
        raise ToolError("new_string must be a string (empty = deletion)")
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical; nothing to do")
    replace_all = args.get("replace_all") is True
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = _get_owned_item(db, item_id, ctx)
        item_id = item["id"]
        if item.get("type") != NOTE_TYPE:
            raise ToolError(
                f"item {item_id} is not a text note "
                f"(type: {item.get('type')})"
            )
    finally:
        db.close()

    content = await _read_note_text(item_id, dek)
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ToolError(
            "old_string not found in the note; re-read it with read_note "
            "and copy a fragment exactly as it appears (paging: the "
            f"fragment may sit outside the fetched window, the note has "
            f"{len(content)} chars)"
        )
    if occurrences > 1 and not replace_all:
        raise ToolError(
            f"old_string matches {occurrences} times; provide a longer, "
            "unique fragment or pass replace_all=true to replace every "
            "occurrence"
        )

    new_content = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    # Revalidate the whole result: NUL bytes, size cap, non-empty note.
    new_content, line_count = _validate_note_content(new_content)

    await _write_note_blob(item_id, new_content, dek)

    db = create_connection()
    try:
        ItemTextRepository(db).update_stats(item_id, len(new_content), line_count)
        ItemRepository(db).touch_updated_at(item_id)
    finally:
        db.close()

    # Minimal ack: echoing content back would only burn tokens.
    return json.dumps({
        "status": "ok",
        "id": short_id(item_id),
        "replacements": occurrences if replace_all else 1,
        "char_count": len(new_content),
        "line_count": line_count,
    }, ensure_ascii=False)


    # Minimal ack: echoing content back would only burn tokens.
    return json.dumps({
        "status": "ok",
        "id": short_id(item_id),
        "replacements": occurrences if replace_all else 1,
        "char_count": len(new_content),
        "line_count": line_count,
    }, ensure_ascii=False)


# ============================================================================
# Memory tools (global - available in every chat while memory is enabled)
# ============================================================================

def _memory_folder_id(db, user_id: int) -> str:
    """The user's memory folder id, or ToolError when memory is off."""
    config = get_memory_config(db, user_id)
    if config is None:
        raise ToolError("memory is not enabled for this user")
    return config["folder_id"]


def _memory_name(name) -> str:
    try:
        return validate_section_name(name)
    except ValueError as exc:
        raise ToolError(str(exc))


def _memory_description(description) -> str:
    try:
        return validate_section_description(description)
    except ValueError as exc:
        raise ToolError(str(exc))


async def _resolve_memory_note(
    db, user_id: int, name: str, dek: bytes, *, create_index: bool = False,
) -> dict:
    """Resolve a memory name to its note item ("MEMORY" = the index).

    ``create_index`` lets writers recreate the index when the user
    deleted it; readers answer with a hint instead.
    """
    folder_id = _memory_folder_id(db, user_id)
    if name.strip().lower() == MEMORY_INDEX_STEM.lower():
        if create_index:
            return await ensure_index_note(db, user_id, folder_id, dek)
        index = find_index_notes(db, folder_id)
        if len(index) > 1:
            raise ToolError(
                "multiple MEMORY.md notes found in the memory folder; "
                "keep exactly one"
            )
        if not index:
            raise ToolError(
                "the memory index MEMORY.md does not exist; it is "
                "recreated automatically on the next write "
                "(create_memory_section / write_memory)"
            )
        return index[0]
    section_name = _memory_name(name)
    matches = await find_section(db, folder_id, section_name, dek)
    if not matches:
        raise ToolError(
            f"memory section not found: {section_name}; read_memory "
            "name='MEMORY' lists the existing sections"
        )
    if len(matches) > 1:
        listed = ", ".join(short_id(m["id"]) for m in matches[:10])
        raise ToolError(
            f"ambiguous memory section '{section_name}': {len(matches)} "
            f"notes match ({listed}); rename the duplicates in the gallery"
        )
    return matches[0]


async def _tool_read_memory(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = await _resolve_memory_note(db, ctx.user_id, name, dek)
        is_index = (item.get("title") or "").lower() == "memory.md" or (
            item.get("original_name") or ""
        ).lower() == "memory.md"
    finally:
        db.close()

    text = await _read_note_text(item["id"], dek)
    meta, _body = parse_frontmatter(text)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    max_chars = _clamp_int(
        args.get("max_chars"), 500, _NOTE_READ_MAX_CHARS,
        _NOTE_READ_DEFAULT_CHARS,
    )
    total = len(text)
    chunk = text[offset:offset + max_chars]
    next_offset = offset + len(chunk)
    return json.dumps({
        "name": (
            MEMORY_INDEX_STEM
            if is_index
            else (meta.get("name") if meta and meta.get("name") else name)
        ),
        "description": meta.get("description") if meta else None,
        "total_chars": total,
        "offset": offset,
        "content": chunk,
        "next_offset": next_offset if next_offset < total else None,
    }, ensure_ascii=False)


async def _tool_create_memory_section(args: dict, ctx: ToolContext) -> str:
    name = _memory_name(args.get("name"))
    description = _memory_description(args.get("description"))
    content = args.get("content")
    if content is not None and (
        not isinstance(content, str) or not content.strip()
    ):
        raise ToolError("content must be a non-empty string when provided")
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        folder_id = _memory_folder_id(db, ctx.user_id)
        if await find_section(db, folder_id, name, dek):
            raise ToolError(
                f"memory section already exists: {name}; change it with "
                "write_memory / edit_memory instead"
            )
        item_id = str(uuid.uuid4())
        title = f"{name}.md"
        text = build_frontmatter(name, description) + (content or "")
        ItemRepository(db).create(
            item_type=NOTE_TYPE,
            folder_id=folder_id,
            user_id=ctx.user_id,
            item_id=item_id,
            title=title,
        )
        ItemTextRepository(db).create(
            item_id=item_id,
            content_type="text/markdown",
            original_name=title,
            char_count=len(text),
            line_count=_count_lines(text),
        )
        # Recreates the index when the user deleted it, then adds the
        # one-line entry under "## Sections".
        await add_index_entry(db, ctx.user_id, folder_id, dek, name, description)
    finally:
        db.close()

    await _write_note_blob(item_id, text, dek)

    return json.dumps({
        "id": short_id(item_id),
        "name": name,
        "description": description,
        "char_count": len(text),
        "line_count": _count_lines(text),
    }, ensure_ascii=False)


async def _tool_write_memory(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    mode = str(args.get("mode") or "replace").strip().lower()
    if mode not in ("replace", "append"):
        raise ToolError("mode must be 'replace' or 'append'")
    content, _ = _validate_note_content(args.get("content"))
    description = None
    if args.get("description") is not None:
        description = _memory_description(args.get("description"))
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = await _resolve_memory_note(
            db, ctx.user_id, name, dek, create_index=True
        )
        item_id = item["id"]
        full = await _read_note_text(item_id, dek)
        meta, body = parse_frontmatter(full)
        if mode == "replace":
            # replace changes the body only: the frontmatter identity is
            # preserved (an optional description refreshes its line).
            if meta is not None:
                section_name = str(meta.get("name") or "").strip()
                section_desc = description if description is not None \
                    else str(meta.get("description") or "").strip()
                new_content = build_frontmatter(section_name, section_desc) \
                    + content
            else:
                new_content = content
        else:
            if meta is not None and description is not None:
                full = build_frontmatter(
                    str(meta.get("name") or "").strip(), description
                ) + body
            new_content = full + content
        new_content, line_count = _validate_note_content(new_content)
    finally:
        db.close()

    await _write_note_blob(item_id, new_content, dek)

    db = create_connection()
    try:
        ItemTextRepository(db).update_stats(
            item_id, len(new_content), line_count
        )
        ItemRepository(db).touch_updated_at(item_id)
    finally:
        db.close()

    # Minimal ack: echoing the content back would only burn tokens.
    return json.dumps({
        "status": "ok",
        "name": name,
        "mode": mode,
        "char_count": len(new_content),
        "line_count": line_count,
    }, ensure_ascii=False)


async def _tool_edit_memory(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not isinstance(old_string, str) or not old_string:
        raise ToolError("old_string must be a non-empty string")
    if not isinstance(new_string, str):
        raise ToolError("new_string must be a string (empty = deletion)")
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical; nothing to do")
    replace_all = args.get("replace_all") is True
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = await _resolve_memory_note(db, ctx.user_id, name, dek)
        item_id = item["id"]
    finally:
        db.close()

    # edit_memory patches the FULL text including the frontmatter, so the
    # model can deliberately fix a description line.
    content = await _read_note_text(item_id, dek)
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ToolError(
            "old_string not found in the memory note; re-read it with "
            "read_memory and copy a fragment exactly as it appears (paging: "
            f"the fragment may sit outside the fetched window, the note has "
            f"{len(content)} chars)"
        )
    if occurrences > 1 and not replace_all:
        raise ToolError(
            f"old_string matches {occurrences} times; provide a longer, "
            "unique fragment or pass replace_all=true to replace every "
            "occurrence"
        )

    new_content = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    meta_old, _ = parse_frontmatter(content)
    if meta_old is not None:
        meta_new, _ = parse_frontmatter(new_content)
        if meta_new is None or str(meta_new.get("name") or "").strip().lower() \
                != str(meta_old.get("name") or "").strip().lower():
            raise ToolError(
                "renaming a memory section by editing its frontmatter is "
                "not supported; create a new section instead"
            )
    # Revalidate the whole result: NUL bytes, size cap, non-empty note.
    new_content, line_count = _validate_note_content(new_content)

    await _write_note_blob(item_id, new_content, dek)

    db = create_connection()
    try:
        ItemTextRepository(db).update_stats(
            item_id, len(new_content), line_count
        )
        ItemRepository(db).touch_updated_at(item_id)
    finally:
        db.close()

    # Minimal ack: echoing content back would only burn tokens.
    return json.dumps({
        "status": "ok",
        "name": name,
        "replacements": occurrences if replace_all else 1,
        "char_count": len(new_content),
        "line_count": line_count,
    }, ensure_ascii=False)


def _tool_create_folder(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    parent_id = args.get("parent_id") or None
    if not name:
        raise ToolError("name is required")

    db = create_connection()
    try:
        if parent_id:
            parent_id = _resolve_folder_id(
                db, ctx.user_id, parent_id, owner_only=True
            )
        try:
            folder = _folder_service(db).create_folder(
                name, ctx.user_id, parent_id
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({"id": short_id(folder["id"]), "name": folder["name"]},
                      ensure_ascii=False)


def _tool_create_album(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    folder_id = args.get("folder_id")
    item_ids = args.get("item_ids") or []
    if not name:
        raise ToolError("name is required")
    if not folder_id:
        raise ToolError("folder_id is required")

    db = create_connection()
    try:
        folder_id = _resolve_folder_id(db, ctx.user_id, folder_id)
        if item_ids:
            item_ids = _resolve_item_id_list(
                db, ctx.user_id, item_ids, "item_ids"
            )
        try:
            album = _album_service(db).create_album(
                name, folder_id, ctx.user_id, item_ids or None
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({
        "id": short_id(album["id"]),
        "name": album["name"],
        "item_count": album.get("item_count", 0),
    }, ensure_ascii=False)


def _tool_add_items_to_album(args: dict, ctx: ToolContext) -> str:
    album_id = str(args.get("album_id") or "").strip()
    item_ids = args.get("item_ids") or []
    if not album_id:
        raise ToolError("album_id is required")
    if not isinstance(item_ids, list) or not item_ids:
        raise ToolError("item_ids must be a non-empty list")

    db = create_connection()
    try:
        album_id = _resolve_album_id(db, ctx.user_id, album_id)
        item_ids = _resolve_item_id_list(db, ctx.user_id, item_ids, "item_ids")
        item_repo = ItemRepository(db)
        media_ids = []
        skipped_notes = 0
        for resolved_id in item_ids:
            item = item_repo.get_by_id(resolved_id)
            if item and item.get("type") != NOTE_TYPE:
                media_ids.append(resolved_id)
            else:
                skipped_notes += 1

        try:
            added = (
                _album_service(db).add_items(album_id, media_ids, ctx.user_id)
                if media_ids else 0
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    result = {"added": added}
    if skipped_notes:
        result["skipped_notes"] = skipped_notes
    return json.dumps(result)


# ============================================================================
# VISION tool
# ============================================================================

def _tool_view_images(args: dict, ctx: ToolContext) -> str:
    item_ids = args.get("item_ids") or []
    reason = str(args.get("reason") or "").strip()
    if not isinstance(item_ids, list) or not (1 <= len(item_ids) <= 8):
        raise ToolError("item_ids must be a list of 1-8 media item ids")
    if not reason:
        raise ToolError("reason is required")

    validated = []
    db = create_connection()
    try:
        for raw_id in item_ids:
            item = _get_owned_item(db, raw_id, ctx)
            if item.get("type") != MEDIA_TYPE or not is_known_item_type(
                item.get("type")
            ):
                raise ToolError(
                    f"item {item['id']} is not a media item; "
                    "notes have no images"
                )
            validated.append(item["id"])
    finally:
        db.close()

    # Pause the turn until the user approves; the orchestrator persists the
    # tool result at resume time.
    raise VisionRequestSignal(validated)


# ============================================================================
# Registry
# ============================================================================

def _build_registry() -> dict:
    tools: list[ToolDef] = [
        ToolDef(
            name="search_items",
            description=(
                "Search the owner's media library by tags. AND between "
                "words, OR within a word group; prefix a tag with '-' to "
                "exclude it (e.g. 'fox night -wolf'). Returns media items "
                "only."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Tag query, e.g. 'fox night -wolf'.",
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "Optional folder id to search within.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                        "description": "Maximum items to return.",
                    },
                },
                "required": ["query"],
            },
            executor=_tool_search_items,
            category="library",
        ),
        ToolDef(
            name="grep_library",
            description=(
                "Full-text search across the library's text data, like grep: "
                "item titles, item descriptions, note bodies, tag names and "
                "descriptions, embedded metadata blocks (PNG text chunks of "
                "media) and original file names. Substring match, case-"
                "insensitive by default; set regex=true for a regular "
                "expression (Python re syntax). Returns matches with short "
                "context snippets — read the whole document with read_note / "
                "get_item_metadata once located. For tag-based media search "
                "use search_items instead."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text to search for.",
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(_GREP_SOURCES)},
                        "description": (
                            "Sources to search; omit to search all."
                        ),
                    },
                    "regex": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Treat pattern as a Python regular expression."
                        ),
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Case-sensitive matching; off by default."
                        ),
                    },
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "Scope item sources to this folder and its "
                            "subfolders; tags stay global."
                        ),
                    },
                    "context_chars": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": _GREP_CONTEXT_MAX_CHARS,
                        "default": _GREP_DEFAULT_CONTEXT_CHARS,
                        "description": (
                            "Context chars around each match in snippets."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                        "description": "Max matches per source.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Page offset per source; echo next_offset."
                        ),
                    },
                },
                "required": ["pattern"],
            },
            executor=_tool_grep_library,
            category="library",
        ),
        ToolDef(
            name="list_folder",
            description=(
                "List the contents of one folder: subfolders, albums and "
                "standalone items. Without folder_id lists the library "
                "root: ALL top-level folders with their albums and items. "
                "Drill into a subfolder id to see its contents."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "Folder id; omit for the library root (all "
                            "top-level folders at once)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 200,
                        "description": "Maximum items to return.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Page offset for the items list; echo "
                            "next_offset from the previous result."
                        ),
                    },
                },
            },
            executor=_tool_list_folder,
            category="library",
        ),
        ToolDef(
            name="get_item",
            description=(
                "Get full metadata of one item (title, description, media "
                "info, explicit and implied tags). For media items also "
                "lists the names of embedded text chunks; fetch their "
                "values with get_item_metadata."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                },
                "required": ["item_id"],
            },
            executor=_tool_get_item,
            category="library",
        ),
        ToolDef(
            name="get_item_metadata",
            description=(
                "Read embedded file metadata of one media item: stored "
                "capture date and size, generation fields (prompt/workflow "
                "PNG text chunks, kept for JXL too) and a live EXIF probe "
                "(camera, GPS, UserComment) for images. Without 'key' "
                "returns a summary with lengths and previews; pass "
                "key='prompt' (etc.) to read one full value in pages. "
                "Some chunks are stored base64 (e.g. 'chara' of a "
                "character-card PNG holds base64 JSON) - set "
                "decode_base64=true to get clean text. "
                "Never use view_images to read text or metadata."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Media item id.",
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Full value to fetch: a text-chunk name "
                            "(e.g. 'prompt', 'workflow') or "
                            "'user_comment', 'image_description', 'xmp'. "
                            "Omit for the summary."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Char offset inside the value; echo "
                            "next_offset from the previous result."
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1000,
                        "maximum": 6000,
                        "default": 6000,
                        "description": "Maximum chars of the value to return.",
                    },
                    "decode_base64": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Decode the stored value from base64 and "
                            "return clean text (offset/max_chars then "
                            "apply to the decoded text). Off by default."
                        ),
                    },
                },
                "required": ["item_id"],
            },
            executor=_tool_get_item_metadata,
            category="library",
        ),
        ToolDef(
            name="set_item_metadata",
            description=(
                "Create or rewrite metadata blocks (PNG text chunks) of "
                "one media item in the library. Values are clean text; "
                "with encode_base64=true each value of this call is "
                "stored base64-encoded (how character cards embed their "
                "'chara' JSON) - read such values back with "
                "get_item_metadata(decode_base64=true). mode 'merge' "
                "(default) adds/overwrites the given keys and keeps the "
                "rest, 'replace_all' makes the block set exactly "
                "'chunks'. Only the library-side copy is updated, never "
                "the file bytes."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Media item id.",
                    },
                    "chunks": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "minProperties": 1,
                        "description": (
                            "Metadata blocks to write, keyed by chunk "
                            "name (e.g. 'prompt', 'workflow', 'chara')."
                        ),
                    },
                    "encode_base64": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Base64-encode every value of this call "
                            "before storing. Off by default."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["merge", "replace_all"],
                        "default": "merge",
                        "description": (
                            "'merge' keeps other existing blocks, "
                            "'replace_all' removes blocks not listed in "
                            "'chunks'."
                        ),
                    },
                },
                "required": ["item_id", "chunks"],
            },
            executor=_tool_set_item_metadata,
            category="items",
            confirmation="always",
        ),
        ToolDef(
            name="read_note",
            description=(
                "Read the text content of one note item (txt/md/yaml/"
                "json/csv stored in the library). Returns a slice plus "
                "next_offset; page through long files by echoing "
                "next_offset. Use get_item_metadata for embedded metadata "
                "and view_images only to actually see an image."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Note item id.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Char offset; echo next_offset from the "
                            "previous read."
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 6000,
                        "default": 4000,
                        "description": "Maximum chars to return.",
                    },
                },
                "required": ["item_id"],
            },
            executor=_tool_read_note,
            category="library",
        ),
        ToolDef(
            name="list_tags",
            description=(
                "Browse existing tags by name substring and/or category, "
                "ordered by usage. To verify a known list of tag names use "
                "get_tag_info with all names in one call instead."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional name substring filter.",
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Optional category filter: slug or name "
                            "(e.g. general, character, species, meta)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                        "description": "Maximum tags to return.",
                    },
                },
            },
            executor=_tool_list_tags,
            category="tags",
        ),
        ToolDef(
            name="get_tag_info",
            description=(
                "Resolve tag names in bulk: for each name return id, "
                "category, usage count and implications (implies / "
                "implied_by). Check ALL candidate names with one call "
                "instead of one call per tag."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 50,
                        "description": (
                            "Tag names to resolve, normalized like "
                            "lower_case_with_underscores."
                        ),
                    },
                    "include_implications": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Include implies/implied_by name lists; set "
                            "false to keep the answer small."
                        ),
                    },
                },
                "required": ["names"],
            },
            executor=_tool_get_tag_info,
            category="tags",
        ),
        ToolDef(
            name="list_folders",
            description="List all folders of the library as a flat list.",
            parameters_json_schema={"type": "object", "properties": {}},
            executor=_tool_list_folders,
            category="library",
        ),
        ToolDef(
            name="list_albums",
            description=(
                "List albums, optionally restricted to one folder. Without "
                "folder_id, albums across the whole library are listed; "
                "page through large libraries by echoing next_offset."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "Optional folder id.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Page offset; echo next_offset from the "
                            "previous result."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                        "description": "Maximum albums per page.",
                    },
                },
            },
            executor=_tool_list_albums,
            category="library",
        ),
        ToolDef(
            name="list_album_items",
            description=(
                "List the items inside one album (id, title, media type, "
                "position), paged. Use list_albums first to find album ids."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "album_id": {
                        "type": "string",
                        "description": "Album id.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Page offset; echo next_offset from the "
                            "previous result."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                        "description": "Maximum items per page.",
                    },
                },
                "required": ["album_id"],
            },
            executor=_tool_list_album_items,
            category="library",
        ),
        ToolDef(
            name="get_tag_suggestions",
            description=(
                "Get statistically related tag suggestions for an item "
                "based on its current tags, library co-occurrence and past "
                "feedback. Tags the user rejected for this item are already "
                "excluded."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                },
                "required": ["item_id"],
            },
            executor=_tool_get_tag_suggestions,
            category="tags",
        ),
        ToolDef(
            name="record_tag_feedback",
            description=(
                "Record feedback on tag suggestions for an item in ONE "
                "batch call. outcome 'accepted' = the suggestion fits; "
                "'rejected' = it does not, and the tag disappears from "
                "future get_tag_suggestions for this item. Accepting does "
                "NOT add the tag to the item — use add_item_tags for that."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                    "decisions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50,
                        "description": (
                            "Batch of decisions; a repeated tag_id resolves "
                            "to the last decision for it."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "tag_id": {
                                    "type": "integer",
                                    "description": (
                                        "Tag id from get_tag_suggestions."
                                    ),
                                },
                                "outcome": {
                                    "type": "string",
                                    "enum": list(_FEEDBACK_OUTCOMES),
                                },
                            },
                            "required": ["tag_id", "outcome"],
                        },
                    },
                },
                "required": ["item_id", "decisions"],
            },
            executor=_tool_record_tag_feedback,
            category="tags",
            confirmation="always",
        ),
        ToolDef(
            name="audit_library",
            description=(
                "Audit the library metadata in ONE call and list items that "
                f"have problems. Checks: {', '.join(AUDIT_CHECKS)}. "
                "Optionally scope to a folder (its whole subtree by "
                "default; include_subfolders=false audits the folder "
                "itself only) and filter by checks. Returns per-check "
                "counts for the whole scope plus one page of offending "
                "items. Always use this instead of enumerating items with "
                "get_item when looking for items with missing metadata."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(AUDIT_CHECKS),
                        },
                        "description": (
                            "Optional subset of checks to run; omit to run "
                            "all of them."
                        ),
                    },
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "Optional folder id; the audit covers this "
                            "folder and all its subfolders. Omit for the "
                            "whole library."
                        ),
                    },
                    "include_subfolders": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "With folder_id: include items of subfolders "
                            "(default) or audit only the folder itself."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                        "description": "Maximum items to return per page.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": (
                            "Page offset for paging through large results."
                        ),
                    },
                },
            },
            executor=_tool_audit_library,
            category="library",
        ),
        ToolDef(
            name="add_item_tags",
            description=(
                "Add existing tags to an item (explicit tags). All tag "
                "names must already exist; if a tag is missing, report it "
                "to the user instead of creating it. Implied tags resolve "
                "automatically - never add implied tags yourself."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 100,
                        "description": "Existing tag names to add.",
                    },
                },
                "required": ["item_id", "tag_names"],
            },
            executor=_tool_add_item_tags,
            category="tags",
            confirmation="always",
        ),
        ToolDef(
            name="remove_item_tags",
            description=(
                "Remove explicit tags from an item. Only existing tags can "
                "be removed."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 30,
                        "description": "Existing tag names to remove.",
                    },
                },
                "required": ["item_id", "tag_names"],
            },
            executor=_tool_remove_item_tags,
            category="tags",
            confirmation="always",
        ),
        ToolDef(
            name="create_tags",
            description=(
                "Create new tags (in the 'general' category unless another "
                "category_id is given) and report which names were "
                "created, already existed or are invalid. The call runs "
                "only after the user confirms it in the chat, so name the "
                "exact tags first."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                        "description": "Tag names to create.",
                    },
                    "category_id": {
                        "type": "integer",
                        "description": (
                            "Optional tag category id; omit for 'general'."
                        ),
                    },
                },
                "required": ["tag_names"],
            },
            executor=_tool_create_tags,
            category="tags",
            confirmation="always",
        ),
        ToolDef(
            name="set_tag_descriptions",
            description=(
                "Set Markdown descriptions for existing tags in bulk (one "
                "call may update several tags). Each description fully "
                "replaces the previous text; an empty string clears it. "
                "Cross-reference related tags with [[tag_name]] "
                "wiki-links. Use standard Markdown lists: nest sub-items "
                "by indenting them with two spaces ('  * sub'), never with "
                "'**' prefixes. Read the current text with get_tag_info "
                "before rewriting an existing description."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": _TAG_DESC_MAX_UPDATES,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "Existing tag name, normalized like "
                                        "lower_case_with_underscores."
                                    ),
                                },
                                "description": {
                                    "type": "string",
                                    "description": (
                                        "Full new Markdown description; an "
                                        "empty string clears the description."
                                    ),
                                },
                            },
                            "required": ["name", "description"],
                        },
                    },
                },
                "required": ["updates"],
            },
            executor=_tool_set_tag_descriptions,
            category="tags",
            confirmation="always",
        ),
        ToolDef(
            name="update_item",
            description="Update an item's title and/or description.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item id.",
                    },
                    "title": {
                        "type": "string",
                        "description": "New title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description.",
                    },
                },
                "required": ["item_id"],
            },
            executor=_tool_update_item,
            category="items",
            confirmation="always",
        ),
        ToolDef(
            name="create_note",
            description=(
                "Create a new text note (a text file) in a folder from "
                "the given content; it is encrypted with the owner's key. "
                "Include a file extension in the title when the format is "
                "clear (e.g. 'notes.yaml')."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "Target folder id.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Note title / file name.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content.",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": list(NOTE_CONTENT_TYPES),
                        "default": "text/plain",
                        "description": "MIME type of the content.",
                    },
                },
                "required": ["folder_id", "title", "content"],
            },
            executor=_tool_create_note,
            category="notes",
            confirmation="always",
        ),
        ToolDef(
            name="write_note",
            description=(
                "Overwrite (mode 'replace', default) or append to an "
                "existing note's text content. 'replace' expects the full "
                "new text; for small targeted changes prefer edit_note. "
                "Returns only status and counters, never the content."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Note item id.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append"],
                        "default": "replace",
                        "description": (
                            "'replace' overwrites the whole note, "
                            "'append' adds to its end."
                        ),
                    },
                },
                "required": ["item_id", "content"],
            },
            executor=_tool_write_note,
            category="notes",
            confirmation="always",
        ),
        ToolDef(
            name="edit_note",
            description=(
                "Make a targeted string replacement inside an existing "
                "note's text: the first exact occurrence of old_string "
                "becomes new_string (every occurrence with "
                "replace_all=true). Copy old_string verbatim from "
                "read_note output and include enough surrounding context "
                "to make it unique - it must match the stored text "
                "exactly. Fails when old_string is not found or matches "
                "more than once without replace_all. Much cheaper than "
                "rewriting the whole note with write_note. Returns only "
                "status and counters."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Note item id.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": (
                            "Exact text to replace, copied verbatim from "
                            "the note."
                        ),
                    },
                    "new_string": {
                        "type": "string",
                        "description": (
                            "Replacement text (empty string deletes the "
                            "fragment)."
                        ),
                    },
                    "replace_all": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Replace every occurrence instead of only "
                            "the first."
                        ),
                    },
                },
                "required": ["item_id", "old_string", "new_string"],
            },
            executor=_tool_edit_note,
            category="notes",
            confirmation="always",
        ),
        ToolDef(
            name="create_folder",
            description="Create a folder, optionally inside a parent folder.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Folder name.",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": (
                            "Optional parent folder id; omit to create a "
                            "root-level folder."
                        ),
                    },
                },
                "required": ["name"],
            },
            executor=_tool_create_folder,
            category="structure",
            confirmation="always",
        ),
        ToolDef(
            name="create_album",
            description=(
                "Create an album in a folder, optionally seeding it with "
                "items that already live in that folder."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Album name.",
                    },
                    "folder_id": {
                        "type": "string",
                        "description": "Folder the album is created in.",
                    },
                    "item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional media item ids to add; they must be in "
                            "the same folder. Notes are skipped."
                        ),
                    },
                },
                "required": ["name", "folder_id"],
            },
            executor=_tool_create_album,
            category="structure",
            confirmation="always",
        ),
        ToolDef(
            name="add_items_to_album",
            description=(
                "Add items to an album. Items must already be in the "
                "album's folder. Albums hold media only - notes are skipped."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "album_id": {
                        "type": "string",
                        "description": "Album id.",
                    },
                    "item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Item ids to add.",
                    },
                },
                "required": ["album_id", "item_ids"],
            },
            executor=_tool_add_items_to_album,
            category="structure",
            confirmation="always",
        ),
        ToolDef(
            name="view_images",
            description=(
                "Request to actually SEE up to 8 images. The user must "
                "approve first; if approved the images are attached to the "
                "next model call. Always give a short reason. Never claim "
                "to see content you have not been shown."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "Media item ids to view.",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Why you need to see these images (shown to "
                            "the user)."
                        ),
                    },
                },
                "required": ["item_ids", "reason"],
            },
            executor=_tool_view_images,
            category="vision",
        ),
        ToolDef(
            name="read_memory",
            description=(
                "Read a memory section by name (paged), or the memory "
                "index with name='MEMORY'. Memory persists between chats "
                "and lives in the user's private memory folder as notes."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Section name, or 'MEMORY' for the index."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Character offset to read from.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": _NOTE_READ_MAX_CHARS,
                        "default": _NOTE_READ_DEFAULT_CHARS,
                        "description": "Max characters per page.",
                    },
                },
                "required": ["name"],
            },
            executor=_tool_read_memory,
            category="memory",
        ),
        ToolDef(
            name="create_memory_section",
            description=(
                "Create a new memory section note: a durable fact about "
                "the user worth remembering across chats. Writes the "
                "section file and adds one index line to MEMORY.md. "
                "Check the index first and extend an existing section "
                "instead of creating near-duplicates."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Section name: letters/digits/dots/underscores/"
                            "dashes, max 64 chars."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "One-line description shown in the index "
                            "(max 200 chars)."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Optional initial Markdown body of the section."
                        ),
                    },
                },
                "required": ["name", "description"],
            },
            executor=_tool_create_memory_section,
            category="memory",
        ),
        ToolDef(
            name="write_memory",
            description=(
                "Rewrite or extend a memory section's body. mode=replace "
                "changes the body only (the name frontmatter is kept; an "
                "optional description refreshes its line), mode=append "
                "adds to the end. name='MEMORY' targets the index itself."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Section name, or 'MEMORY' for the index."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "New (or appended) Markdown body.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append"],
                        "default": "replace",
                        "description": "replace or append.",
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Optional new one-line index description."
                        ),
                    },
                },
                "required": ["name", "content"],
            },
            executor=_tool_write_memory,
            category="memory",
        ),
        ToolDef(
            name="edit_memory",
            description=(
                "Targeted exact-string patch inside a memory note "
                "(including its frontmatter, e.g. to fix the "
                "description line): the first occurrence of old_string "
                "becomes new_string, every occurrence with "
                "replace_all=true. Read the note first and copy the "
                "fragment verbatim."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Section name, or 'MEMORY' for the index."
                        ),
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact fragment to replace.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement (empty = deletion).",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Replace every occurrence instead of the first."
                        ),
                    },
                },
                "required": ["name", "old_string", "new_string"],
            },
            executor=_tool_edit_memory,
            category="memory",
        ),
        FETCH_URL_DEF,
        WEB_SEARCH_DEF,
    ]
    return {tool.name: tool for tool in tools}


TOOL_REGISTRY: dict = _build_registry()
