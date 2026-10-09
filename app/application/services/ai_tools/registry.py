"""AI tool registry - the tools the chat model may call.

Every executor opens its own database connection (closed in ``finally``),
scopes every query by the requesting user and returns a plain-text JSON
result for the model. Long results are truncated by the chat orchestrator,
not here.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException

from ....database import create_connection
from ....infrastructure.repositories import (
    AUDIT_CHECKS,
    AlbumRepository,
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
    PermissionRepository,
    TagCooccurrenceRepository,
    TagFeedbackRepository,
    TagImplicationRepository,
    TagMutexRepository,
    TagsRepository,
)
from ..album_service import AlbumService
from ..folder_service import FolderService
from ..item_service import ItemService
from ..item_types import is_known_item_type
from ..tag_service import TagService
from ..tag_suggestion_service import TagSuggestionService
from .base import ToolContext, ToolDef, ToolError, VisionRequestSignal

MEDIA_TYPE = "media"


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
    finally:
        db.close()

    return json.dumps({
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
                "info, explicit and implied tags)."
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
