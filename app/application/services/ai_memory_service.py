"""AI memory service - per-user persistent memory stored in the gallery.

Memory is a per-user opt-in: one private folder holds a ``MEMORY.md``
index note (always embedded into the chat system prompt while memory is
enabled) and any number of section notes ``{name}.md`` carrying a small
frontmatter block (``name`` / ``description``). Everything is a regular
note: the user sees and edits them like any other note.

This module holds the shared logic used by the settings route, the
memory tools (``ai_tools/registry.py``) and the chat orchestrator. It
deliberately imports only repositories/encryption/storage - importing
the ``ai_tools`` package here would be circular (the registry imports
application services). Failures are raised as :class:`ValueError` and
translated into ``ToolError`` by the tools.
"""
from __future__ import annotations

import io
import re
import uuid
from typing import Optional

from ...infrastructure.repositories import (
    AiChatSettingsRepository,
    FolderRepository,
    ItemRepository,
    ItemTextRepository,
)
from ...infrastructure.services.encryption import EncryptionService
from ...infrastructure.storage import get_storage

# The index note's reserved name: a section can never be called MEMORY.
MEMORY_INDEX_STEM = "MEMORY"
MEMORY_INDEX_NAME = MEMORY_INDEX_STEM + ".md"

# The system prompt embeds the index, so it is capped independently of
# TOOL_RESULT_LIMIT (which only applies to tool results).
MEMORY_INDEX_LIMIT = 8000

# Section names become file names; keep them filename-safe and short.
MEMORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MEMORY_DESCRIPTION_MAX_CHARS = 200
MEMORY_WHEN_TO_USE_MAX_CHARS = 200
MEMORY_SUMMARY_MAX_CHARS = 1000

# The index entry carries the "Use when:" scenario on an indented line
# right below the bullet; both lines belong to the entry block.
_WHEN_TO_USE_LABEL = "Use when:"

# Frontmatter fallback resolution decrypts note bodies; the scan is
# capped so a huge folder cannot turn one tool call into a full sweep.
_FRONTMATTER_SCAN_LIMIT = 500
_FRONTMATTER_PROBE_CHARS = 2048

MEMORY_INDEX_TEMPLATE = (
    "# Memory Index\n"
    "\n"
    "## Summary\n"
    "\n"
    "(Brief summary about the user - keep it to 1-2 sentences and update "
    "it as you learn more.)\n"
    "\n"
    "## Sections\n"
)


class MemoryValidationError(ValueError):
    """Invalid memory section name/description (tools map to ToolError)."""


# ============================================================================
# Settings / configuration
# ============================================================================

def get_memory_config(db, user_id: int) -> Optional[dict]:
    """Return the user's memory config when memory is usable, else None.

    Read-time revalidation: the folder must still exist and belong to the
    user (a dangling reference after ``transfer_ownership`` or manual DB
    edits counts as disabled).

    Returns:
        ``{"folder_id": ..., "folder_name": ...}`` or None.
    """
    settings = AiChatSettingsRepository(db).ensure(user_id)
    if not settings.get("memory_enabled") or not settings.get("memory_folder_id"):
        return None
    folder_id = settings["memory_folder_id"]
    folder = FolderRepository(db).get_by_id(folder_id)
    if folder is None or folder.get("user_id") != user_id:
        return None
    return {"folder_id": folder_id, "folder_name": folder.get("name")}


# ============================================================================
# Frontmatter helpers
# ============================================================================

def build_frontmatter(
    name: str, description: str, when_to_use: Optional[str] = None
) -> str:
    """The frontmatter block a section note starts with (body follows)."""
    block = f"---\nname: {name}\ndescription: {description}"
    if when_to_use:
        block += f"\nwhen_to_use: {when_to_use}"
    return block + "\n---\n\n"


def parse_frontmatter(text: str) -> tuple[Optional[dict], str]:
    """Split a note into ``(frontmatter, body)``.

    Returns ``(None, text)`` when the text does not start with a
    well-formed frontmatter block. Unknown keys are preserved.
    """
    if not text.startswith("---\n"):
        return None, text
    rest = text[4:]
    end = rest.find("\n---")
    while end != -1:
        after = end + 4
        if after >= len(rest) or rest[after] == "\n":
            block = rest[:end]
            body = rest[after + 1:] if after < len(rest) else ""
            # build_frontmatter separates the fence and the body with a
            # blank line; drop that one newline so the roundtrip is exact.
            if body.startswith("\n"):
                body = body[1:]
            meta: dict = {}
            for line in block.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            return meta, body
        end = rest.find("\n---", end + 1)
    return None, text


def strip_leading_frontmatter(text: str) -> str:
    """Return *text* without its leading frontmatter block, if any.

    If *text* starts with a well-formed ``---`` fence pair the metadata
    is dropped and only the body is returned.  A malformed opening fence
    (no closing ``---``) passes through untouched.
    """
    meta, body = parse_frontmatter(text)
    return body if meta is not None else text


def validate_section_name(name) -> str:
    """Validate a section name; MEMORY is reserved for the index.

    Raises:
        MemoryValidationError: Empty, wrong pattern or reserved.
    """
    value = str(name or "").strip()
    if not value:
        raise MemoryValidationError("memory section name is required")
    if not MEMORY_NAME_PATTERN.match(value):
        raise MemoryValidationError(
            "memory section name must start with a letter or digit and "
            "contain only letters, digits, dots, underscores and dashes "
            "(max 64 chars)"
        )
    if value.lower() == MEMORY_INDEX_STEM.lower():
        raise MemoryValidationError(
            f"'{MEMORY_INDEX_STEM}' is reserved for the memory index"
        )
    return value


def validate_section_description(description) -> str:
    """Validate a one-line description (newlines are stripped out)."""
    value = re.sub(r"\s*\r?\n\s*", " ", str(description or "").strip())
    if not value:
        raise MemoryValidationError("memory section description is required")
    if len(value) > MEMORY_DESCRIPTION_MAX_CHARS:
        raise MemoryValidationError(
            "memory section description must be at most "
            f"{MEMORY_DESCRIPTION_MAX_CHARS} characters"
        )
    return value


def validate_section_when_to_use(when_to_use) -> str:
    """Validate the one-line "Use when:" scenario (newlines stripped)."""
    value = re.sub(r"\s*\r?\n\s*", " ", str(when_to_use or "").strip())
    if not value:
        raise MemoryValidationError(
            "memory section when_to_use is required: name the situations "
            "the section applies to"
        )
    if len(value) > MEMORY_WHEN_TO_USE_MAX_CHARS:
        raise MemoryValidationError(
            "memory section when_to_use must be at most "
            f"{MEMORY_WHEN_TO_USE_MAX_CHARS} characters"
        )
    return value


def validate_summary(summary) -> str:
    """Validate the index Summary (one line, newlines stripped)."""
    value = re.sub(r"\s*\r?\n\s*", " ", str(summary or "").strip())
    if not value:
        raise MemoryValidationError("memory summary is required")
    if len(value) > MEMORY_SUMMARY_MAX_CHARS:
        raise MemoryValidationError(
            "memory summary must be at most "
            f"{MEMORY_SUMMARY_MAX_CHARS} characters"
        )
    return value


# ============================================================================
# Blob helpers (same envelope as note uploads)
# ============================================================================

def _count_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1)


async def _write_note_bytes(item_id: str, content: str, dek: bytes) -> None:
    """Encrypt note text with ``dek`` and store it as the item's blob."""
    envelope = EncryptionService.encrypt_bytes(content.encode("utf-8"), dek)
    await get_storage().upload(item_id, io.BytesIO(envelope), folder="uploads")


async def read_note_text(item_id: str, dek: bytes) -> str:
    """Download, decrypt and decode a note's stored blob (always UTF-8)."""
    blob = await get_storage().download(item_id, folder="uploads")
    return EncryptionService.decrypt_bytes(blob, dek).decode(
        "utf-8", errors="replace"
    )


async def read_index_text(item_id: str, dek: bytes) -> str:
    """The index text as embedded into the system prompt (capped)."""
    text = await read_note_text(item_id, dek)
    if len(text) > MEMORY_INDEX_LIMIT:
        text = (
            text[:MEMORY_INDEX_LIMIT]
            + f"\n\n[... index truncated at {MEMORY_INDEX_LIMIT} chars ...]"
        )
    return text


# ============================================================================
# Index note
# ============================================================================

def find_index_notes(db, folder_id: str) -> list[dict]:
    """All notes in the folder named ``MEMORY.md`` (title or original_name).

    The schema does not enforce uniqueness, so the list can hold more
    than one row - callers treat that as an ambiguity error.
    """
    rows = db.execute(
        "SELECT i.* FROM items i JOIN item_texts t ON t.item_id = i.id "
        "WHERE i.folder_id = ? AND i.type = 'note' AND "
        "(LOWER(i.title) = ? OR LOWER(t.original_name) = ?)",
        (folder_id, MEMORY_INDEX_NAME.lower(), MEMORY_INDEX_NAME.lower()),
    ).fetchall()
    return [dict(row) for row in rows]


async def ensure_index_note(
    db, user_id: int, folder_id: str, dek: bytes
) -> dict:
    """Return the folder's MEMORY.md item, creating it when missing.

    Recreating a deleted index keeps memory self-healing: the model and
    the settings route both call this before touching the index.

    Raises:
        ValueError: More than one MEMORY.md note exists in the folder.
    """
    notes = find_index_notes(db, folder_id)
    if len(notes) > 1:
        raise ValueError(
            "multiple MEMORY.md notes found in the memory folder; "
            "keep exactly one"
        )
    if notes:
        return notes[0]

    item_id = str(uuid.uuid4())
    ItemRepository(db).create(
        item_type="note",
        folder_id=folder_id,
        user_id=user_id,
        item_id=item_id,
        title=MEMORY_INDEX_NAME,
    )
    ItemTextRepository(db).create(
        item_id=item_id,
        content_type="text/markdown",
        original_name=MEMORY_INDEX_NAME,
        char_count=len(MEMORY_INDEX_TEMPLATE),
        line_count=_count_lines(MEMORY_INDEX_TEMPLATE),
    )
    await _write_note_bytes(item_id, MEMORY_INDEX_TEMPLATE, dek)
    return ItemRepository(db).get_by_id(item_id)


def _index_entry_block(
    name: str, description: str, when_to_use: Optional[str] = None
) -> list[str]:
    """The index lines one section occupies (bullet + Use-when line)."""
    lines = [f"- **{name}** — {description}"]
    if when_to_use:
        lines.append(f"  {_WHEN_TO_USE_LABEL} {when_to_use}")
    return lines


def _entry_block_end(lines: list[str], bullet_index: int) -> int:
    """One past the last line of the entry block starting at bullet_index.

    The block is the bullet plus the indented continuation lines that
    directly follow it (the "Use when:" line and legacy hand-wrapped
    continuations).
    """
    end = bullet_index + 1
    while (
        end < len(lines)
        and lines[end].startswith("  ")
        and lines[end].strip()
    ):
        end += 1
    return end


def _upsert_index_entry(text: str, name: str, lines: list[str]) -> str:
    """Insert or refresh the section's entry block in the index text."""
    all_lines = text.splitlines()
    prefix = f"- **{name}**"
    for i, existing in enumerate(all_lines):
        if existing == prefix or existing.startswith(prefix + " "):
            all_lines[i:_entry_block_end(all_lines, i)] = lines
            return "\n".join(all_lines) + ("\n" if text.endswith("\n") else "")
    insert_at = len(all_lines)
    in_sections = False
    for i, existing in enumerate(all_lines):
        heading = existing.strip()
        if heading == "## Sections":
            in_sections = True
            continue
        if in_sections and heading.startswith("## "):
            insert_at = i
            break
    if not in_sections:
        sep = "" if text.endswith("\n") else "\n"
        return f"{text}{sep}\n## Sections\n\n" + "\n".join(lines) + "\n"
    all_lines[insert_at:insert_at] = lines
    return "\n".join(all_lines) + ("\n" if text.endswith("\n") else "")


def remove_index_entry(text: str, name: str) -> str:
    """Drop the section's whole entry block from the index text.

    A name that has no entry leaves the text unchanged (the index is
    self-healing and may not know about a hand-created note).
    """
    all_lines = text.splitlines()
    prefix = f"- **{name}**"
    for i, existing in enumerate(all_lines):
        if existing == prefix or existing.startswith(prefix + " "):
            del all_lines[i:_entry_block_end(all_lines, i)]
            break
    return "\n".join(all_lines) + ("\n" if text.endswith("\n") else "")


def set_index_summary(text: str, summary: str) -> str:
    """Replace the text under the ``## Summary`` heading of the index.

    The heading (and a ``# Memory Index`` title) are recreated when the
    user hand-stripped them, so the tool always produces a parseable
    index.
    """
    all_lines = text.splitlines()
    for i, line in enumerate(all_lines):
        if line.strip() == "## Summary":
            end = len(all_lines)
            for j in range(i + 1, len(all_lines)):
                if all_lines[j].strip().startswith("## "):
                    end = j
                    break
            tail = all_lines[end:]
            # The trailing blank line only makes sense when a heading
            # follows; at the very end of the note it would pile up.
            block = ["", summary] + ([""] if tail else [])
            all_lines = all_lines[:i + 1] + block + tail
            return "\n".join(all_lines) + ("\n" if text.endswith("\n") else "")
    # No "## Summary" heading: recreate it right after the title (or at
    # the top when even that is gone), skipping any blank lines that
    # directly follow the title.
    title_at = None
    for i, line in enumerate(all_lines):
        if line.strip() == "# Memory Index":
            title_at = i
            break
    if title_at is None:
        head = ["# Memory Index", ""]
        insert_at = 0
    else:
        head = []
        insert_at = title_at + 1
        while insert_at < len(all_lines) and not all_lines[insert_at].strip():
            insert_at += 1
    block = ["## Summary", "", summary, ""]
    all_lines = head + all_lines[:insert_at] + block + all_lines[insert_at:]
    return "\n".join(all_lines) + ("\n" if text.endswith("\n") else "")


async def add_index_entry(
    db, user_id: int, folder_id: str, dek: bytes,
    name: str, description: str, when_to_use: Optional[str] = None,
) -> dict:
    """Add (or refresh) the section's entry block in the index.

    The index is recreated when the user deleted it. The updated note is
    persisted (blob + stats + updated_at) and returned.
    """
    item = await ensure_index_note(db, user_id, folder_id, dek)
    text = await read_note_text(item["id"], dek)
    new_text = _upsert_index_entry(
        text, name, _index_entry_block(name, description, when_to_use)
    )
    await _write_note_bytes(item["id"], new_text, dek)
    ItemTextRepository(db).update_stats(
        item["id"], len(new_text), _count_lines(new_text)
    )
    ItemRepository(db).touch_updated_at(item["id"])
    return ItemRepository(db).get_by_id(item["id"])


async def remove_index_entry_from_note(
    db, user_id: int, folder_id: str, dek: bytes, name: str,
) -> dict:
    """Drop the section's entry block from the stored index note."""
    item = await ensure_index_note(db, user_id, folder_id, dek)
    text = await read_note_text(item["id"], dek)
    new_text = remove_index_entry(text, name)
    if new_text != text:
        await _write_note_bytes(item["id"], new_text, dek)
        ItemTextRepository(db).update_stats(
            item["id"], len(new_text), _count_lines(new_text)
        )
        ItemRepository(db).touch_updated_at(item["id"])
    return ItemRepository(db).get_by_id(item["id"])


async def write_index_summary(
    db, user_id: int, folder_id: str, dek: bytes, summary: str,
) -> dict:
    """Replace the Summary block of the stored index note."""
    item = await ensure_index_note(db, user_id, folder_id, dek)
    text = await read_note_text(item["id"], dek)
    new_text = set_index_summary(text, summary)
    await _write_note_bytes(item["id"], new_text, dek)
    ItemTextRepository(db).update_stats(
        item["id"], len(new_text), _count_lines(new_text)
    )
    ItemRepository(db).touch_updated_at(item["id"])
    return ItemRepository(db).get_by_id(item["id"])


# ============================================================================
# Section notes
# ============================================================================

async def find_section(
    db, folder_id: str, name: str, dek: bytes
) -> list[dict]:
    """All notes in the memory folder that resolve to the section name.

    Matched by ``{name}.md`` title/original_name (case-insensitive) or,
    as a fallback, by the ``name:`` frontmatter field of the note body.
    Callers handle 0 / 1 / N results (not found / found / ambiguous).
    """
    target = f"{name}.md".lower()
    rows = db.execute(
        "SELECT i.*, t.original_name FROM items i "
        "JOIN item_texts t ON t.item_id = i.id "
        "WHERE i.folder_id = ? AND i.type = 'note' ORDER BY i.uploaded_at",
        (folder_id,),
    ).fetchall()
    matches: list[dict] = []
    scanned: list[dict] = []
    for row in rows:
        item = dict(row)
        title = (item.get("title") or "").lower()
        original = (item.get("original_name") or "").lower()
        if title == target or original == target:
            matches.append(item)
        else:
            scanned.append(item)
    if matches:
        return matches
    if dek and len(scanned) <= _FRONTMATTER_SCAN_LIMIT:
        for item in scanned:
            try:
                text = await read_note_text(item["id"], dek)
            except Exception:
                continue
            meta, _body = parse_frontmatter(text[:_FRONTMATTER_PROBE_CHARS])
            if meta and str(meta.get("name") or "").lower() == name.lower():
                matches.append(item)
    return matches
