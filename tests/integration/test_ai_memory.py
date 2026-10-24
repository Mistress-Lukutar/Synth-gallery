"""Integration tests for AI memory (migration 0014).

Covers the memory settings route (privacy/ownership validation, index
creation, re-enable), the memory tools (create/write/edit, name
resolution, reserved MEMORY name), the memory block in the system
prompt, the global-tool bypass of agent allow-lists, the sharing guard
and the folder-delete reset.
"""
import asyncio
import json

import pytest

from app.application.services import ai_chat_service as ai_chat_service_module
from app.application.services.ai_memory_service import (
    find_index_notes,
    get_memory_config,
)
from app.application.services.ai_tools import (
    MEMORY_TOOL_NAMES,
    TOOL_REGISTRY,
    ToolContext,
    ToolError,
)
from app.infrastructure.repositories import (
    FolderRepository,
    ItemRepository,
    ItemTextRepository,
)
from app.infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)
from app.infrastructure.services.llm.types import (
    StopReason,
    ToolCall,
    ToolCallsReady,
    TurnComplete,
)

from .test_ai_chat import (
    FakeLLMClient,
    _configure_provider,
    _create_conversation,
    _post_message,
    _text_turn,
)
# Fixtures re-exported from the chat test module (pytest resolves them by
# name in this module's namespace).
from .test_ai_chat import csrf, dek  # noqa: F401

DEK = bytes(range(32))


def _ctx(user_id: int) -> ToolContext:
    return ToolContext(user_id=user_id, dek=DEK)


def _call(name: str, args: dict, user_id: int) -> dict:
    result = TOOL_REGISTRY[name].executor(args, _ctx(user_id))
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return json.loads(result)


def _enable_memory(db, user_id: int, folder_id: str) -> None:
    repo = AiChatSettingsRepository(db)
    repo.ensure(user_id)
    repo.update(
        user_id, {"memory_enabled": 1, "memory_folder_id": folder_id}
    )


def _disable_memory(db, user_id: int) -> None:
    repo = AiChatSettingsRepository(db)
    repo.ensure(user_id)
    repo.update(user_id, {"memory_enabled": 0})


def _make_folder(db, user_id: int, name: str = "Memory") -> str:
    return FolderRepository(db).create(name, user_id)


def _index_text(db, folder_id: str, dek: bytes = DEK) -> str:
    import asyncio as aio

    from app.application.services.ai_memory_service import read_note_text

    notes = find_index_notes(db, folder_id)
    assert len(notes) == 1
    return aio.run(read_note_text(notes[0]["id"], dek))


def _note_body(db, folder_id: str, title: str, dek: bytes = DEK) -> str:
    import asyncio as aio

    from app.application.services.ai_memory_service import read_note_text

    row = db.execute(
        "SELECT i.id FROM items i WHERE i.folder_id = ? AND i.title = ?",
        (folder_id, title),
    ).fetchone()
    assert row is not None, f"note {title} not found"
    return aio.run(read_note_text(row["id"], dek))


# ============================================================================
# Settings route
# ============================================================================

def test_memory_enable_requires_folder(authenticated_client, csrf):
    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True},
        headers=csrf,
    )
    assert response.status_code == 400


def test_memory_enable_rejects_foreign_and_shared_folders(
    authenticated_client, csrf, db_connection, test_user, second_user, dek
):
    # A folder owned by someone else.
    foreign = _make_folder(db_connection, second_user["id"], "Foreign")
    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True, "folder_id": foreign},
        headers=csrf,
    )
    assert response.status_code == 400

    # An own folder shared with a viewer.
    shared = _make_folder(db_connection, test_user["id"], "Shared")
    grant = authenticated_client.post(
        f"/api/folders/{shared}/permissions",
        json={"user_id": second_user["id"], "permission": "viewer"},
        headers=csrf,
    )
    assert grant.status_code == 200
    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True, "folder_id": shared},
        headers=csrf,
    )
    assert response.status_code == 400

    settings = authenticated_client.get("/api/user/ai/settings").json()
    assert settings["settings"]["memory_enabled"] in (0, None, False)


def test_memory_enable_creates_index_and_disable_keeps_folder(
    authenticated_client, csrf, db_connection, test_user, dek
):
    folder_id = _make_folder(db_connection, test_user["id"])

    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True, "folder_id": folder_id},
        headers=csrf,
    )
    assert response.status_code == 200, response.text
    settings = response.json()["settings"]
    assert settings["memory_enabled"] == 1
    assert settings["memory_folder_id"] == folder_id

    index = _index_text(db_connection, folder_id, dek)
    assert "# Memory Index" in index
    assert "## Sections" in index

    # Disabling keeps the folder for a later re-enable.
    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": False},
        headers=csrf,
    )
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["memory_enabled"] == 0
    assert settings["memory_folder_id"] == folder_id

    # Re-enable without folder_id falls back to the stored folder.
    response = authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True},
        headers=csrf,
    )
    assert response.status_code == 200
    assert response.json()["settings"]["memory_enabled"] == 1


def test_memory_folder_cannot_be_shared(
    authenticated_client, csrf, db_connection, test_user, second_user, dek
):
    folder_id = _make_folder(db_connection, test_user["id"])
    assert authenticated_client.put(
        "/api/user/ai/settings/memory",
        json={"enabled": True, "folder_id": folder_id},
        headers=csrf,
    ).status_code == 200

    response = authenticated_client.post(
        f"/api/folders/{folder_id}/permissions",
        json={"user_id": second_user["id"], "permission": "viewer"},
        headers=csrf,
    )
    assert response.status_code == 403
    assert "memory" in response.json()["detail"].lower()

    # After disabling memory the folder can be shared again.
    _disable_memory(db_connection, test_user["id"])
    db_connection.commit()
    response = authenticated_client.post(
        f"/api/folders/{folder_id}/permissions",
        json={"user_id": second_user["id"], "permission": "viewer"},
        headers=csrf,
    )
    assert response.status_code == 200


def test_folder_delete_resets_memory_settings(
    authenticated_client, csrf, db_connection, test_user, dek
):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    response = authenticated_client.delete(
        f"/api/folders/{folder_id}", headers=csrf
    )
    assert response.status_code == 200

    row = db_connection.execute(
        "SELECT memory_enabled, memory_folder_id FROM ai_chat_settings "
        "WHERE user_id = ?",
        (test_user["id"],),
    ).fetchone()
    assert row["memory_enabled"] == 0
    assert row["memory_folder_id"] is None


# ============================================================================
# Tools
# ============================================================================

def test_memory_tools_disabled_without_config(db_connection, test_user):
    with pytest.raises(ToolError, match="memory is not enabled"):
        _call("read_memory", {"name": "MEMORY"}, test_user["id"])


def test_create_memory_section_writes_index(
    db_connection, test_user
):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    result = _call("create_memory_section", {
        "name": "project-alpha",
        "description": "the alpha rendering project",
        "content": "Alpha uses ComfyUI workflows.",
    }, test_user["id"])
    assert result["name"] == "project-alpha"

    index = _index_text(db_connection, folder_id)
    assert "- **project-alpha** — the alpha rendering project" in index

    body = _note_body(db_connection, folder_id, "project-alpha.md")
    assert body.startswith("---\nname: project-alpha\n")
    assert "description: the alpha rendering project" in body
    assert "Alpha uses ComfyUI workflows." in body


def test_create_memory_section_recreates_deleted_index(
    db_connection, test_user
):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    _call("create_memory_section", {
        "name": "likes", "description": "user preferences",
    }, test_user["id"])
    # User deletes the index note (rows; a note has no media detail).
    row = find_index_notes(db_connection, folder_id)[0]
    db_connection.execute(
        "DELETE FROM item_texts WHERE item_id = ?", (row["id"],)
    )
    db_connection.execute("DELETE FROM items WHERE id = ?", (row["id"],))
    db_connection.commit()
    assert find_index_notes(db_connection, folder_id) == []

    _call("create_memory_section", {
        "name": "dislikes", "description": "user dislikes",
    }, test_user["id"])
    # The index is rebuilt from the template: the new entry is there,
    # entries of the deleted index are not restored.
    index = _index_text(db_connection, folder_id)
    assert "# Memory Index" in index
    assert "- **dislikes** — user dislikes" in index
    assert "- **likes**" not in index


def test_write_memory_replace_keeps_frontmatter(db_connection, test_user):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    _call("create_memory_section", {
        "name": "style",
        "description": "rendering style prefs",
        "content": "old body",
    }, test_user["id"])

    result = _call("write_memory", {
        "name": "style", "content": "new body", "mode": "replace",
    }, test_user["id"])
    assert result["status"] == "ok"

    body = _note_body(db_connection, folder_id, "style.md")
    assert body.startswith("---\nname: style\n")
    assert "description: rendering style prefs" in body
    assert "new body" in body
    assert "old body" not in body

    # append keeps the frontmatter and adds to the end.
    _call("write_memory", {
        "name": "style", "content": "more", "mode": "append",
        "description": "updated description",
    }, test_user["id"])
    body = _note_body(db_connection, folder_id, "style.md")
    assert "new body" in body and body.rstrip().endswith("more")
    assert "description: updated description" in body


def test_edit_memory_patches_full_text(db_connection, test_user):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    _call("create_memory_section", {
        "name": "tools",
        "description": "tools the user likes",
        "content": "Uses Zed.",
    }, test_user["id"])

    # A description fix through the frontmatter is allowed.
    result = _call("edit_memory", {
        "name": "tools",
        "old_string": "description: tools the user likes",
        "new_string": "description: editor + render tools",
    }, test_user["id"])
    assert result["status"] == "ok"

    body = _note_body(db_connection, folder_id, "tools.md")
    assert "description: editor + render tools" in body
    assert "Uses Zed." in body

    # Renaming through the frontmatter is rejected.
    with pytest.raises(ToolError, match="renaming"):
        _call("edit_memory", {
            "name": "tools",
            "old_string": "name: tools",
            "new_string": "name: tools2",
        }, test_user["id"])


def test_memory_name_resolution_and_reserved_index(db_connection, test_user):
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    _call("create_memory_section", {
        "name": "alpha", "description": "alpha section", "content": "A",
    }, test_user["id"])

    # The reserved index name reads MEMORY.md.
    index = _call("read_memory", {"name": "MEMORY"}, test_user["id"])
    assert index["name"] == "MEMORY"
    assert "- **alpha** — alpha section" in index["content"]

    # Case-insensitive section lookup.
    section = _call("read_memory", {"name": "ALPHA"}, test_user["id"])
    assert section["name"] == "alpha"
    assert "A" in section["content"]

    # Unknown section.
    with pytest.raises(ToolError, match="not found"):
        _call("read_memory", {"name": "missing"}, test_user["id"])

    # Reserved name cannot become a section.
    with pytest.raises(ToolError, match="reserved"):
        _call("create_memory_section", {
            "name": "MEMORY", "description": "x",
        }, test_user["id"])

    # Invalid names are rejected.
    with pytest.raises(ToolError, match="name must"):
        _call("create_memory_section", {
            "name": "has space", "description": "x",
        }, test_user["id"])

    # Duplicates are rejected.
    with pytest.raises(ToolError, match="already exists"):
        _call("create_memory_section", {
            "name": "alpha", "description": "again",
        }, test_user["id"])

    # A second note matching the same name makes reads ambiguous.
    dup_id = ItemRepository(db_connection).create(
        item_type="note", folder_id=folder_id, user_id=test_user["id"],
        title="alpha.md",
    )
    ItemTextRepository(db_connection).create(
        item_id=dup_id, content_type="text/plain", original_name="alpha.md",
        char_count=0, line_count=0,
    )
    db_connection.commit()
    with pytest.raises(ToolError, match="ambiguous"):
        _call("read_memory", {"name": "alpha"}, test_user["id"])


# ============================================================================
# Orchestrator integration
# ============================================================================

def test_system_prompt_embeds_index_and_tools(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    _post_message(authenticated_client, csrf, conversation["id"], "hi")

    request = fake.requests[0]
    assert "Memory is enabled" in request.system_prompt
    assert "# Memory Index" in request.system_prompt
    assert folder_id[:12] in request.system_prompt
    tool_names = {spec.name for spec in request.tools}
    assert MEMORY_TOOL_NAMES <= tool_names


def test_memory_tools_bypass_agent_allow_list(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    folder_id = _make_folder(db_connection, test_user["id"])
    _enable_memory(db_connection, test_user["id"], folder_id)
    db_connection.commit()

    created = authenticated_client.post(
        "/api/user/ai/agents",
        json={
            "name": "Narrow",
            "system_prompt": "You are Narrow.",
            "tools": ["list_tags"],
        },
        headers=csrf,
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    db_connection.execute(
        "UPDATE ai_conversations SET agent_id = ? WHERE id = ?",
        (agent_id, conversation["id"]),
    )
    db_connection.commit()
    _post_message(authenticated_client, csrf, conversation["id"], "hi")

    request = fake.requests[0]
    tool_names = {spec.name for spec in request.tools}
    # Memory tools join the narrow allow-list...
    assert MEMORY_TOOL_NAMES <= tool_names
    assert "list_tags" in tool_names
    # ...everything else stays filtered out.
    assert "view_images" not in tool_names
    assert "You are Narrow." in request.system_prompt
