"""AI tool registry - the tools the chat model may call.

Every executor opens its own database connection (closed in ``finally``),
scopes every query by the requesting user and returns a plain-text JSON
result for the model. Long results are truncated by the chat orchestrator,
not here.
"""
from __future__ import annotations

import io
import json
import uuid
from typing import Optional

from fastapi import HTTPException

from ....config import TEXT_MAX_SIZE
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
from ..folder_service import FolderService
from ..item_service import ItemService
from ..item_types import is_known_item_type
from ..tag_service import TagService
from ..tag_suggestion_service import TagSuggestionService
from .base import ToolContext, ToolDef, ToolError, VisionRequestSignal

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
# Live EXIF probing decrypts the whole image into memory; skip huge files.
_EXIF_PROBE_MAX_BYTES = 256 * 1024 * 1024


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


def _get_owned_item(item_repo: ItemRepository, item_id, ctx: ToolContext) -> dict:
    """Return the item row when it exists and belongs to the caller."""
    item = item_repo.get_by_id(str(item_id))
    if item is None or item.get("user_id") != ctx.user_id:
        raise ToolError(f"item not found: {item_id}")
    return item


def _resolve_root_folder(folder_service: FolderService, user_id: int) -> str:
    """Find the user's root folder id (a folder without a parent)."""
    for node in folder_service.get_folder_tree(user_id):
        if node.get("parent_id") is None and node.get("user_id") == user_id:
            return node["id"]
    raise ToolError("no root folder found; the library is empty")


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


def _page_text(value: str, args: dict, key_label: str) -> str:
    """Slice a full metadata value into one paged tool result."""
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    max_chars = _clamp_int(
        args.get("max_chars"), 1000, _METADATA_KEY_MAX_CHARS,
        _METADATA_KEY_MAX_CHARS,
    )
    total = len(value)
    chunk = value[offset:offset + max_chars]
    next_offset = offset + len(chunk)
    return json.dumps({
        "key": key_label,
        "value": chunk,
        "total_chars": total,
        "next_offset": next_offset if next_offset < total else None,
    }, ensure_ascii=False)


def _metadata_fetch_key(key: str, chunks: dict, args: dict) -> str:
    """Return the full paged value of one text chunk."""
    if key in chunks:
        return _page_text(chunks[key], args, key)
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


# ============================================================================
# READ tools
# ============================================================================

def _tool_search_items(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    folder_id = args.get("folder_id") or None
    limit = _clamp_int(args.get("limit"), 1, 50, 20)

    db = create_connection()
    try:
        result = _tag_service(db).search_items(query, folder_id, sort_by="uploaded")
    finally:
        db.close()

    items = []
    for row in result.get("items", [])[:limit]:
        items.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "type": MEDIA_TYPE,
            "media_type": row.get("media_type"),
            "content_type": row.get("content_type"),
            "folder_id": row.get("folder_id"),
            "uploaded_at": str(row.get("uploaded_at")) if row.get("uploaded_at") else None,
        })
    return json.dumps({
        "items": items,
        "total": result.get("total", len(items)),
        "note": "tag search; prefix a tag with '-' to exclude",
    }, ensure_ascii=False)


def _tool_list_folder(args: dict, ctx: ToolContext) -> str:
    folder_id = args.get("folder_id") or None
    limit = _clamp_int(args.get("limit"), 1, 200, 200)

    db = create_connection()
    try:
        service = _folder_service(db)
        if folder_id is None:
            folder_id = _resolve_root_folder(service, ctx.user_id)
        try:
            contents = service.get_folder_contents(folder_id, ctx.user_id)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    items = contents.get("items", [])
    return json.dumps({
        "subfolders": [
            {"id": f["id"], "name": f["name"]}
            for f in contents.get("subfolders", [])[:limit]
        ],
        "albums": [
            {"id": a["id"], "name": a["name"], "item_count": a.get("item_count", 0)}
            for a in contents.get("albums", [])
        ],
        "items": [
            {
                "id": i["id"],
                "title": i.get("title"),
                "type": i.get("type"),
                "media_type": i.get("media_type"),
            }
            for i in items[:limit]
        ],
        "truncated": len(items) > limit,
    }, ensure_ascii=False)


def _tool_get_item(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")

    db = create_connection()
    try:
        item_repo = ItemRepository(db)
        _get_owned_item(item_repo, item_id, ctx)
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
        "id": item["id"],
        "title": item.get("title"),
        "description": item.get("description"),
        "type": item.get("type"),
        "folder_id": item.get("folder_id"),
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
        item = _get_owned_item(ItemRepository(db), item_id, ctx)
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
    if key:
        if key in chunks:
            return _metadata_fetch_key(key, chunks, args)
        if key in ("user_comment", "image_description", "xmp"):
            live = await _probe_exif(item_id, media)
            value = live.get(key)
            if not value:
                raise ToolError(f"item {item_id} has no {key}")
            return _page_text(value, args, key)
        raise ToolError(
            f"unknown metadata key '{key}'; available: "
            + (
                ", ".join(sorted(chunks))
                if chunks
                else "(none) or user_comment / image_description / xmp"
            )
        )

    summary = {
        "id": item_id,
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


async def _tool_read_note(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")
    dek = _require_dek(ctx)

    db = create_connection()
    try:
        item = _get_owned_item(ItemRepository(db), item_id, ctx)
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
        "id": item_id,
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

    db = create_connection()
    try:
        result = _tag_service(db).list_tags(query=query, limit=limit)
    finally:
        db.close()

    return json.dumps({
        "tags": [
            {
                "id": t["id"],
                "name": t["name"],
                "category": t.get("category_name"),
                "description": t.get("description"),
                "usage_count": t.get("usage_count", 0),
            }
            for t in result.get("items", [])
        ],
        "total": result.get("total", 0),
    }, ensure_ascii=False)


def _tool_get_tag_info(args: dict, ctx: ToolContext) -> str:
    name = _normalize_tag_name(args.get("name"))
    if not name:
        raise ToolError("name is required")

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        matches = tags_repo.get_by_name(name)
        if not matches:
            raise ToolError(
                f"tag '{name}' not found; use create_tags to create it first"
            )
        tag = matches[0]
        implications = _tag_service(db).get_tag_implications(tag["id"])
    finally:
        db.close()

    return json.dumps({
        "id": tag["id"],
        "name": tag["name"],
        "description": tag.get("description"),
        "category": tag.get("category_name"),
        "usage_count": tag.get("usage_count", 0),
        "implies": [t["name"] for t in implications.get("implies", [])],
        "implied_by": [t["name"] for t in implications.get("implied_by", [])],
    }, ensure_ascii=False)


def _tool_list_folders(args: dict, ctx: ToolContext) -> str:
    db = create_connection()
    try:
        tree = _folder_service(db).get_folder_tree(ctx.user_id)
    finally:
        db.close()

    return json.dumps({
        "folders": [
            {"id": f["id"], "name": f["name"], "parent_id": f.get("parent_id")}
            for f in tree
            if f.get("user_id") == ctx.user_id
        ]
    }, ensure_ascii=False)


def _tool_list_albums(args: dict, ctx: ToolContext) -> str:
    folder_id = args.get("folder_id") or None
    cap = 50

    albums = []
    db = create_connection()
    try:
        album_service = _album_service(db)
        folder_service = _folder_service(db)
        if folder_id is not None:
            rows = album_service.get_albums_by_folder(folder_id, ctx.user_id)
            albums.extend(rows)
        else:
            for node in folder_service.get_folder_tree(ctx.user_id):
                if node.get("user_id") != ctx.user_id:
                    continue
                if len(albums) >= cap:
                    break
                try:
                    contents = folder_service.get_folder_contents(
                        node["id"], ctx.user_id
                    )
                except HTTPException:
                    continue
                albums.extend(contents.get("albums", []))
    finally:
        db.close()

    albums = albums[:cap]
    return json.dumps({
        "albums": [
            {
                "id": a["id"],
                "name": a["name"],
                "folder_id": a.get("folder_id"),
                "item_count": a.get("item_count", 0),
            }
            for a in albums
        ]
    }, ensure_ascii=False)


def _tool_get_tag_suggestions(args: dict, ctx: ToolContext) -> str:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        raise ToolError("item_id is required")

    try:
        db = create_connection()
        try:
            _get_owned_item(ItemRepository(db), item_id, ctx)
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
    limit = _clamp_int(args.get("limit"), 1, 100, 50)
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)

    db = create_connection()
    try:
        folder_ids = None
        if folder_id is not None:
            folder_repo = FolderRepository(db)
            folder = folder_repo.get_by_id(str(folder_id))
            if folder is None or folder.get("user_id") != ctx.user_id:
                raise ToolError(f"folder not found: {folder_id}")
            folder_ids = folder_repo.get_subtree_ids(folder["id"])
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
                "id": row["id"],
                "title": row["title"],
                "type": row["type"],
                "media_type": row.get("media_type"),
                "folder_id": row["folder_id"],
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
        result["folder_id"] = str(folder_id)
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
    if not isinstance(tag_names, list) or not (1 <= len(tag_names) <= 30):
        raise ToolError("tag_names must be a list of 1-30 tag names")

    db = create_connection()
    try:
        tags_repo = TagsRepository(db)
        tag_service = _tag_service(db)
        _get_owned_item(ItemRepository(db), item_id, ctx)
        unique, found = _resolve_unknown_tags(tags_repo, tag_names)
        if unique:
            unknown = [n for n in unique if not found.get(n)]
            if unknown:
                raise ToolError(
                    "unknown tags: " + ", ".join(unknown)
                    + ". Use create_tags to create them first."
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
        _get_owned_item(ItemRepository(db), item_id, ctx)
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
        _get_owned_item(item_repo, item_id, ctx)
        # update_metadata writes title/description and bumps updated_at.
        item_repo.update_metadata(item_id, title=title, description=description)
        updated = item_repo.get_by_id(item_id)
    finally:
        db.close()

    return json.dumps({
        "id": item_id,
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
        "id": item_id,
        "title": title,
        "folder_id": folder_id,
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
        item = _get_owned_item(ItemRepository(db), item_id, ctx)
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
        "id": item_id,
        "char_count": len(content),
        "line_count": line_count,
    }, ensure_ascii=False)


def _tool_create_folder(args: dict, ctx: ToolContext) -> str:
    name = str(args.get("name") or "").strip()
    parent_id = args.get("parent_id") or None
    if not name:
        raise ToolError("name is required")

    db = create_connection()
    try:
        try:
            folder = _folder_service(db).create_folder(
                name, ctx.user_id, parent_id
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({"id": folder["id"], "name": folder["name"]},
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
        try:
            album = _album_service(db).create_album(
                name, folder_id, ctx.user_id, item_ids or None
            )
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({
        "id": album["id"],
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
        try:
            added = _album_service(db).add_items(album_id, item_ids, ctx.user_id)
        except HTTPException as exc:
            raise _http_to_tool_error(exc)
    finally:
        db.close()

    return json.dumps({"added": added})


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
        item_repo = ItemRepository(db)
        for raw_id in item_ids:
            item_id = str(raw_id)
            item = _get_owned_item(item_repo, item_id, ctx)
            if item.get("type") != MEDIA_TYPE or not is_known_item_type(
                item.get("type")
            ):
                raise ToolError(
                    f"item {item_id} is not a media item; "
                    "notes have no images"
                )
            validated.append(item_id)
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
        ),
        ToolDef(
            name="list_folder",
            description=(
                "List the contents of one folder: subfolders, albums and "
                "standalone items. Omit folder_id to list the library root."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "Folder id; omit for the library root."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 200,
                        "description": "Maximum items to return.",
                    },
                },
            },
            executor=_tool_list_folder,
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
                },
                "required": ["item_id"],
            },
            executor=_tool_get_item_metadata,
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
        ),
        ToolDef(
            name="list_tags",
            description=(
                "List existing tags (optionally filtered by name substring) "
                "with usage counts. Use this to check which tags exist."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional name substring filter.",
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
        ),
        ToolDef(
            name="get_tag_info",
            description=(
                "Get one tag by exact name: description, category, usage "
                "count and its implications (implies / implied_by)."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact tag name.",
                    },
                },
                "required": ["name"],
            },
            executor=_tool_get_tag_info,
        ),
        ToolDef(
            name="list_folders",
            description="List all folders of the library as a flat list.",
            parameters_json_schema={"type": "object", "properties": {}},
            executor=_tool_list_folders,
        ),
        ToolDef(
            name="list_albums",
            description=(
                "List albums, optionally restricted to one folder. Without "
                "folder_id, albums across the whole library are listed."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": "Optional folder id.",
                    },
                },
            },
            executor=_tool_list_albums,
        ),
        ToolDef(
            name="get_tag_suggestions",
            description=(
                "Get statistically related tag suggestions for an item "
                "based on its current tags and library co-occurrence."
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
        ),
        ToolDef(
            name="audit_library",
            description=(
                "Audit the library metadata in ONE call and list items that "
                f"have problems. Checks: {', '.join(AUDIT_CHECKS)}. "
                "Optionally scope to a folder (its whole subtree) and filter "
                "by checks. Returns per-check counts for the whole scope "
                "plus one page of offending items. Always use this instead "
                "of enumerating items with get_item when looking for items "
                "with missing metadata."
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
        ),
        ToolDef(
            name="add_item_tags",
            description=(
                "Add existing tags to an item (explicit tags). All tag "
                "names must already exist; create missing ones with "
                "create_tags first. Implied tags resolve automatically - "
                "never add implied tags yourself."
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
                        "description": "Existing tag names to add.",
                    },
                },
                "required": ["item_id", "tag_names"],
            },
            executor=_tool_add_item_tags,
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
        ),
        ToolDef(
            name="create_tags",
            description=(
                "Create new tags (in the 'general' category unless another "
                "category_id is given) and report which names were "
                "created, already existed or are invalid."
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
        ),
        ToolDef(
            name="write_note",
            description=(
                "Overwrite (mode 'replace', default) or append to an "
                "existing note's text content. 'replace' expects the full "
                "new text; there is no partial editing. Returns only "
                "status and counters, never the content."
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
                            "Optional item ids to add; they must be in the "
                            "same folder."
                        ),
                    },
                },
                "required": ["name", "folder_id"],
            },
            executor=_tool_create_album,
        ),
        ToolDef(
            name="add_items_to_album",
            description=(
                "Add items to an album. Items must already be in the "
                "album's folder."
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
        ),
    ]
    return {tool.name: tool for tool in tools}


TOOL_REGISTRY: dict = _build_registry()
