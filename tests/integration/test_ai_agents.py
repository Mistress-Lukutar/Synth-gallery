"""Integration tests for AI agents: CRUD, chat integration, confirmations.

Covers built-in seeding, agent CRUD (built-ins immutable), conversations
bound to an agent (tool filtering + prompt), and the mutation-approval
flow: mutating tools pause the turn for the user's decision, with an
optional "always allow in this chat" memory.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.application.services import ai_chat_service as ai_chat_service_module
from app.application.services.ai_agent_service import BUILTIN_AGENTS
from app.application.services.ai_tools import (
    MEMORY_TOOL_NAMES,
    TOOL_REGISTRY,
    ToolContext,
)
from app.config import CSRF_COOKIE_NAME
from app.infrastructure.repositories import (
    ItemMediaRepository,
    ItemRepository,
)
from app.infrastructure.services.llm.types import (
    StopReason,
    ToolCall,
    ToolCallsReady,
    ToolResult,
    TurnComplete,
)

from .test_ai_chat import (
    FakeLLMClient,
    _assert_ok,
    _configure_provider,
    _create_conversation,
    _events_by_name,
    _parse_sse,
    _post_message,
    _text_turn,
)
# Fixtures re-exported from the chat test module (pytest resolves them by
# name in this module's namespace).
from .test_ai_chat import csrf, dek, general_category  # noqa: F401


def _list_agents(client: TestClient) -> list:
    response = client.get("/api/user/ai/agents")
    assert response.status_code == 200, response.text
    return response.json()["agents"]


def _agent_by_key(agents, builtin_key):
    for agent in agents:
        if agent.get("builtin_key") == builtin_key:
            return agent
    return None


# ============================================================================
# 1. Built-in seeding
# ============================================================================

def test_builtin_agents_seeded_on_list(authenticated_client):
    agents = _list_agents(authenticated_client)
    assert len(agents) == len(BUILTIN_AGENTS)
    keys = {agent["builtin_key"] for agent in agents}
    assert keys == {"tagger", "librarian", "curator"}
    for agent in agents:
        assert agent["is_builtin"] is True
        assert agent["tools"], "built-ins must allow at least one tool"

    tagger = _agent_by_key(agents, "tagger")
    assert "add_item_tags" in tagger["tools"]
    assert "create_tags" not in tagger["tools"]
    assert "You are Tagger" in tagger["system_prompt"]

    curator = _agent_by_key(agents, "curator")
    assert "create_tags" in curator["tools"]

    librarian = _agent_by_key(agents, "librarian")
    assert all(
        tool not in librarian["tools"]
        for tool in ("add_item_tags", "create_tags", "update_item")
    )


def test_tools_catalogue_in_list_response(authenticated_client):
    response = authenticated_client.get("/api/user/ai/agents")
    tools = response.json()["tools"]
    # Memory tools are global and hidden from the agent editor catalogue.
    assert {t["name"] for t in tools} == set(TOOL_REGISTRY) - MEMORY_TOOL_NAMES
    for tool in tools:
        assert tool["category"]
        assert tool["confirmation"] in ("none", "always")
    by_name = {t["name"]: t for t in tools}
    assert by_name["search_items"]["confirmation"] == "none"
    assert by_name["add_item_tags"]["confirmation"] == "always"


def test_builtin_seeding_is_idempotent(authenticated_client):
    first = _list_agents(authenticated_client)
    second = _list_agents(authenticated_client)
    assert [a["id"] for a in first] == [a["id"] for a in second]


# ============================================================================
# 2. Agent CRUD
# ============================================================================

def test_agent_create_update_delete_cycle(authenticated_client, csrf):
    created = authenticated_client.post(
        "/api/user/ai/agents",
        json={
            "name": "My agent",
            "description": "does one thing",
            "system_prompt": "You are Mine.",
            "icon": "star",
            "color_index": 3,
            "tools": ["search_items", "list_tags"],
        },
        headers=csrf,
    )
    assert created.status_code == 200, created.text
    agent = created.json()["agent"]
    assert agent["is_builtin"] is False
    assert agent["builtin_key"] is None
    assert agent["tools"] == ["search_items", "list_tags"]

    updated = authenticated_client.put(
        f"/api/user/ai/agents/{agent['id']}",
        json={"name": "Renamed", "tools": ["search_items"]},
        headers=csrf,
    )
    assert updated.status_code == 200
    assert updated.json()["agent"]["name"] == "Renamed"
    assert updated.json()["agent"]["tools"] == ["search_items"]

    agents = _list_agents(authenticated_client)
    assert any(a["id"] == agent["id"] for a in agents)

    deleted = authenticated_client.delete(
        f"/api/user/ai/agents/{agent['id']}", headers=csrf
    )
    assert deleted.status_code == 200
    agents = _list_agents(authenticated_client)
    assert all(a["id"] != agent["id"] for a in agents)


def test_agent_create_rejects_unknown_tools(authenticated_client, csrf):
    response = authenticated_client.post(
        "/api/user/ai/agents",
        json={"name": "Bad", "tools": ["list_tags", "not_a_tool"]},
        headers=csrf,
    )
    assert response.status_code == 400
    assert "not_a_tool" in response.json()["detail"]


def test_agent_create_rejects_empty_name_and_bad_icon(authenticated_client, csrf):
    assert (
        authenticated_client.post(
            "/api/user/ai/agents", json={"name": "  "}, headers=csrf
        ).status_code
        == 400
    )
    assert (
        authenticated_client.post(
            "/api/user/ai/agents", json={"name": "X", "icon": "rocket"},
            headers=csrf,
        ).status_code
        == 400
    )


def test_builtin_agents_cannot_be_edited_or_deleted(authenticated_client, csrf):
    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")

    updated = authenticated_client.put(
        f"/api/user/ai/agents/{tagger['id']}", json={"name": "Hacked"},
        headers=csrf,
    )
    assert updated.status_code == 403

    deleted = authenticated_client.delete(
        f"/api/user/ai/agents/{tagger['id']}", headers=csrf
    )
    assert deleted.status_code == 403


def test_duplicate_builtin_creates_custom_copy(authenticated_client, csrf):
    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")
    response = authenticated_client.post(
        f"/api/user/ai/agents/{tagger['id']}/duplicate", headers=csrf
    )
    assert response.status_code == 200
    copy = response.json()["agent"]
    assert copy["is_builtin"] is False
    assert copy["builtin_key"] is None
    assert copy["name"] == "Copy of Tagger"
    assert copy["tools"] == tagger["tools"]

    # The copy is editable and deletable.
    updated = authenticated_client.put(
        f"/api/user/ai/agents/{copy['id']}", json={"name": "My Tagger"},
        headers=csrf,
    )
    assert updated.status_code == 200
    deleted = authenticated_client.delete(
        f"/api/user/ai/agents/{copy['id']}", headers=csrf
    )
    assert deleted.status_code == 200


# ============================================================================
# 2b. Active agent selection (preselected for new chats)
# ============================================================================

def test_active_agent_persists_and_resets(authenticated_client, csrf, dek):
    """PUT /agents/active stores the draft persona; null returns to
    Assistant. The endpoint must stay declared before PUT /{agent_id}:
    otherwise "active" is parsed as an id and the call 422s while the
    settings row keeps its old value (regression the browser smoke caught)."""
    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")

    response = authenticated_client.put(
        "/api/user/ai/agents/active",
        json={"active_agent_id": tagger["id"]},
        headers=csrf,
    )
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["active_agent_id"] == tagger["id"]

    # The chat panel reads the preselection from the settings endpoint.
    loaded = authenticated_client.get("/api/user/ai/settings")
    assert loaded.status_code == 200
    assert loaded.json()["settings"]["active_agent_id"] == tagger["id"]

    reset = authenticated_client.put(
        "/api/user/ai/agents/active",
        json={"active_agent_id": None},
        headers=csrf,
    )
    assert reset.status_code == 200
    assert reset.json()["settings"]["active_agent_id"] is None


def test_active_agent_unknown_id_rejected(authenticated_client, csrf):
    response = authenticated_client.put(
        "/api/user/ai/agents/active",
        json={"active_agent_id": 424242},
        headers=csrf,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown agent"


def test_deleting_agent_clears_active_selection(authenticated_client, csrf, dek):
    created = authenticated_client.post(
        "/api/user/ai/agents",
        json={"name": "Doomed", "tools": ["search_items"]},
        headers=csrf,
    )
    assert created.status_code == 200
    agent = created.json()["agent"]

    put = authenticated_client.put(
        "/api/user/ai/agents/active",
        json={"active_agent_id": agent["id"]},
        headers=csrf,
    )
    assert put.status_code == 200

    deleted = authenticated_client.delete(
        f"/api/user/ai/agents/{agent['id']}", headers=csrf
    )
    assert deleted.status_code == 200

    settings = authenticated_client.get("/api/user/ai/settings").json()
    assert settings["settings"]["active_agent_id"] is None


# ============================================================================
# 3. Conversations bound to an agent
# ============================================================================

def test_create_conversation_with_agent_and_foreign_rejected(
    authenticated_client, csrf
):
    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")

    created = authenticated_client.post(
        "/api/ai/chat/conversations",
        json={"agent_id": tagger["id"]},
        headers=csrf,
    )
    assert created.status_code == 200
    conversation = created.json()["conversation"]
    assert conversation["agent_id"] == tagger["id"]

    listed = authenticated_client.get("/api/ai/chat/conversations").json()[
        "conversations"
    ]
    assert any(c["id"] == conversation["id"] for c in listed)

    unknown = authenticated_client.post(
        "/api/ai/chat/conversations", json={"agent_id": 99999}, headers=csrf
    )
    assert unknown.status_code == 400


# ============================================================================
# 4. Agent turn: tool filtering + prompt
# ============================================================================

def test_agent_turn_filters_tools_and_uses_agent_prompt(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )

    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")
    conversation = _create_conversation(authenticated_client, csrf)

    # Bind the agent directly in storage (mirrors the UI flow, which sends
    # agent_id at conversation creation).
    db_connection.execute(
        "UPDATE ai_conversations SET agent_id = ? WHERE id = ?",
        (tagger["id"], conversation["id"]),
    )
    db_connection.commit()

    _assert_ok(
        _post_message(authenticated_client, csrf, conversation["id"], "tag it")
    )

    request = fake.requests[0]
    advertised = {spec.name for spec in request.tools}
    assert advertised == set(tagger["tools"])
    assert "create_tags" not in advertised
    assert "You are Tagger" in request.system_prompt
    # The generic identity line must not leak into an agent conversation.
    assert "You are the AI assistant of Synth Gallery" not in request.system_prompt


def test_tool_outside_agent_allowlist_is_denied(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(
        [
            [
                ToolCallsReady(
                    [ToolCall("c1", "create_tags", '{"tag_names": ["x"]}')],
                    "",
                ),
                TurnComplete(StopReason.TOOL_USE),
            ],
            _text_turn("understood"),
        ]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )

    tagger = _agent_by_key(_list_agents(authenticated_client), "tagger")
    created = authenticated_client.post(
        "/api/ai/chat/conversations",
        json={"agent_id": tagger["id"]},
        headers=csrf,
    )
    conversation = created.json()["conversation"]

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "create a tag"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "not enabled" in results[0]["summary"]

    # The model was told about the denial and the turn finished normally.
    resumed = fake.requests[1]
    tool_parts = [
        part
        for turn in resumed.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert tool_parts and tool_parts[0].is_error is True
    assert "not enabled" in tool_parts[0].content


# ============================================================================
# 5. Mutation confirmation flow
# ============================================================================

def _seed_item_with_tag(db_connection, test_user, test_folder, tag_names):
    """Create one owned media item plus existing tags; returns the item id."""
    from app.application.services.ai_tools import TOOL_REGISTRY

    item_id = ItemRepository(db_connection).create(
        item_type="media",
        folder_id=test_folder,
        user_id=test_user["id"],
        title="img.jpg",
    )
    ItemMediaRepository(db_connection).create(
        item_id=item_id, media_type="image", content_type="image/jpeg"
    )
    TOOL_REGISTRY["create_tags"].executor(
        {"tag_names": list(tag_names)}, ToolContext(user_id=test_user["id"])
    )
    return item_id


def _item_tag_names(db_connection, item_id):
    rows = db_connection.execute(
        "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
        "WHERE it.item_id = ?",
        (item_id,),
    ).fetchall()
    return sorted(row["name"] for row in rows)


def _confirmation_script(item_id, tag_name):
    return [
        [
            ToolCallsReady(
                [
                    ToolCall(
                        "m1",
                        "add_item_tags",
                        json.dumps({"item_id": item_id, "tag_names": [tag_name]}),
                    )
                ],
                "",
            ),
            TurnComplete(StopReason.TOOL_USE),
        ],
        _text_turn("tagged"),
    ]


def _start_confirmation_turn(authenticated_client, csrf, db_connection,
                             test_user, dek, monkeypatch, item_id, tag_name):
    """Send a message whose tool call pauses for confirmation; returns
    (conversation, request_id)."""
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(_confirmation_script(item_id, tag_name))
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "tag this"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    confirms = _events_by_name(events, "tool_confirm")
    assert len(confirms) == 1
    payload = confirms[0]
    assert payload["name"] == "add_item_tags"
    assert payload["arguments"] == {
        "item_id": item_id, "tag_names": [tag_name],
    }
    # Paused: no result and no turn_done yet, and nothing was executed.
    assert _events_by_name(events, "tool_result") == []
    assert _events_by_name(events, "turn_done") == []
    assert _item_tag_names(db_connection, item_id) == []
    return conversation, payload["request_id"]


def test_tool_confirmation_approved_executes(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, general_category,
):
    item_id = _seed_item_with_tag(db_connection, test_user, test_folder, ["sunset"])
    conversation, request_id = _start_confirmation_turn(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, item_id, "sunset",
    )

    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/tools/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["is_error"] is False
    assert events[-1][0] == "turn_done"

    # The mutation really happened.
    assert _item_tag_names(db_connection, item_id) == ["sunset"]

    messages = db_connection.execute(
        "SELECT role, parts FROM ai_messages WHERE conversation_id = ? "
        "ORDER BY id",
        (conversation["id"],),
    ).fetchall()
    roles = [row["role"] for row in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]

    assert not ai_chat_service_module._active_users
    assert not ai_chat_service_module._pending_confirmations


def test_tool_confirmation_denied_blocks_mutation(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, general_category,
):
    item_id = _seed_item_with_tag(db_connection, test_user, test_folder, ["sunset"])
    conversation, request_id = _start_confirmation_turn(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, item_id, "sunset",
    )

    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/tools/{request_id}/decision",
        json={"approved": False},
        headers=csrf,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "denied" in results[0]["summary"].lower()
    assert events[-1][0] == "turn_done"

    assert _item_tag_names(db_connection, item_id) == []


def test_tool_confirmation_always_allow_skips_later_cards(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, general_category,
):
    item_id = _seed_item_with_tag(
        db_connection, test_user, test_folder, ["sunset", "sky"]
    )
    _configure_provider(db_connection, test_user["id"], dek)

    tool_turn_sunset = _confirmation_script(item_id, "sunset")[0]
    tool_turn_sky = _confirmation_script(item_id, "sky")[0]
    fake = FakeLLMClient(
        [tool_turn_sunset, _text_turn("tagged"),
         tool_turn_sky, _text_turn("again")]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    # First turn: pauses for confirmation.
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "tag this"
    )
    events = _parse_sse(response.text)
    request_id = _events_by_name(events, "tool_confirm")[0]["request_id"]

    decision = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/tools/{request_id}/decision",
        json={"approved": True, "always_allow": True},
        headers=csrf,
    )
    assert decision.status_code == 200
    decision_events = _parse_sse(decision.text)
    assert decision_events[-1][0] == "turn_done"
    assert _item_tag_names(db_connection, item_id) == ["sunset"]

    # Second turn: the same tool runs without another confirmation card.
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "tag it again"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert _events_by_name(events, "tool_confirm") == []
    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["is_error"] is False
    assert events[-1][0] == "turn_done"
    assert _item_tag_names(db_connection, item_id) == ["sky", "sunset"]


def test_tool_confirmation_unknown_404_and_expired_410(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, general_category,
):
    item_id = _seed_item_with_tag(db_connection, test_user, test_folder, ["sunset"])
    conversation, request_id = _start_confirmation_turn(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, item_id, "sunset",
    )

    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}/tools/nope/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 404

    pending = ai_chat_service_module._pending_confirmations[request_id]
    pending.created_at -= (
        ai_chat_service_module.AiChatService.CONFIRMATION_TTL_SECONDS + 10
    )
    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/tools/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 410


def test_remaining_batch_calls_execute_after_approval(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, general_category,
):
    """A mutating call followed by a read call in one batch: the read runs
    after the approval instead of being lost."""
    item_id = _seed_item_with_tag(db_connection, test_user, test_folder, ["sunset"])
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(
        [
            [
                ToolCallsReady(
                    [
                        ToolCall(
                            "m1",
                            "add_item_tags",
                            json.dumps(
                                {"item_id": item_id, "tag_names": ["sunset"]}
                            ),
                        ),
                        ToolCall("r1", "list_tags", "{}"),
                    ],
                    "",
                ),
                TurnComplete(StopReason.TOOL_USE),
            ],
            _text_turn("done"),
        ]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "tag it"
    )
    events = _parse_sse(response.text)
    request_id = _events_by_name(events, "tool_confirm")[0]["request_id"]

    decision = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/tools/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert decision.status_code == 200
    decision_events = _parse_sse(decision.text)

    results = _events_by_name(decision_events, "tool_result")
    assert [r["is_error"] for r in results] == [False, False]
    assert {r["name"] for r in results} == {"add_item_tags", "list_tags"}
    assert decision_events[-1][0] == "turn_done"
    assert _item_tag_names(db_connection, item_id) == ["sunset"]


def test_dangling_agent_id_falls_back_to_default_assistant(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    db_connection.execute(
        "UPDATE ai_conversations SET agent_id = 424242 WHERE id = ?",
        (conversation["id"],),
    )
    db_connection.commit()

    _assert_ok(
        _post_message(authenticated_client, csrf, conversation["id"], "hi")
    )
    request = fake.requests[0]
    # Memory is disabled here: the default assistant sees every tool
    # except the global memory tools.
    assert {spec.name for spec in request.tools} == (
        set(TOOL_REGISTRY) - MEMORY_TOOL_NAMES
    )
    assert "You are the AI assistant of Synth Gallery" in request.system_prompt
