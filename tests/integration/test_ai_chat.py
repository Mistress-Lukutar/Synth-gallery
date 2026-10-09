"""Integration tests for the built-in AI chat orchestrator.

Covers the chat repository, the streamed turn pipeline (plain answers,
tool flows, vision approval/denial, iteration cap) and the /api/ai/chat
routes, using a scripted fake LLM client patched in at the service's
single ``_get_llm_client`` access point.
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.application.services import ai_chat_service as ai_chat_service_module
from app.application.services.ai_chat_service import (
    AiChatService,
    VisionRequestNotFoundError,
)
from app.application.services.ai_provider_service import AiProviderService
from app.config import CSRF_COOKIE_NAME
from app.database import create_connection
from app.infrastructure.repositories import (
    AiChatRepository,
    AiChatSettingsRepository,
    AiModelRepository,
    AiProviderRepository,
    FolderRepository,
    ItemMediaRepository,
    ItemRepository,
    UserRepository,
)
from app.infrastructure.services.encryption import (
    EncryptionService,
    dek_cache,
)
from app.infrastructure.services.llm.types import (
    ImagePart,
    StopReason,
    TextDelta,
    ToolCall,
    ToolCallsReady,
    ToolResult,
    TurnComplete,
)
from app.routes.ai_chat import build_ai_chat_service

from ..conftest import login_as

CSRF = {"X-CSRF-Token": ""}


# ============================================================================
# Fake LLM client
# ============================================================================

class FakeLLMClient:
    """Scripted stand-in for the provider LLM clients.

    ``script`` is a list of turns; each turn is a list of stream events
    yielded by one ``stream_chat`` call.
    """

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        for event in self.script.pop(0):
            yield event

    async def fetch_models(self, base_url, api_key):
        raise NotImplementedError


# ============================================================================
# Helpers / fixtures
# ============================================================================

def _parse_sse(text: str):
    """Parse an SSE body into a list of (event_name, payload_dict)."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = None
        payload = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
        events.append((name, payload))
    return events


def _events_by_name(events, name):
    return [payload for event_name, payload in events if event_name == name]


def _text_turn(text, in_tokens=10, out_tokens=5):
    return [
        TextDelta(text),
        TurnComplete(StopReason.END_TURN, in_tokens, out_tokens),
    ]


def _configure_provider(db_connection, user_id, dek):
    """Create provider + model + active selection for the user."""
    provider_repo = AiProviderRepository(db_connection)
    provider_id = provider_repo.create(
        user_id=user_id,
        label="Test Provider",
        protocol="openai_compatible",
        base_url="https://api.example.com/v1",
        # get_active_endpoint decrypts the blob with the DEK, so it must
        # be a real envelope even though the fake client never sends it.
        api_key_encrypted=EncryptionService.encrypt_bytes(b"sk-test-key", dek),
    )
    AiModelRepository(db_connection).replace_for_provider(
        provider_id,
        [
            {
                "model_id": "test-model",
                "display_name": "Test Model",
                "supports_tools": 1,
                "supports_vision": 1,
            }
        ],
    )
    settings = AiChatSettingsRepository(db_connection)
    settings.ensure(user_id)
    settings.update(
        user_id,
        {
            "active_provider_id": provider_id,
            "active_model_id": "test-model",
        },
    )
    return provider_id


@pytest.fixture
def dek(authenticated_client: TestClient, test_user):
    """The user's real DEK (login populated the session cache)."""
    key = dek_cache.get(test_user["id"])
    assert key is not None, "login must populate the DEK cache"
    yield key


@pytest.fixture
def csrf(csrf_token: str):
    return {"X-CSRF-Token": csrf_token}


@pytest.fixture
def general_category(db_connection):
    """Fresh databases have no tag categories; create 'general' (slug id 1)."""
    db_connection.execute(
        "INSERT INTO tag_categories (id, slug, name, color, sort_order) "
        "VALUES (1, 'general', 'General', '#6b7280', 1)"
    )
    db_connection.commit()


def _create_conversation(client: TestClient, csrf_headers) -> dict:
    response = client.post(
        "/api/ai/chat/conversations", json={}, headers=csrf_headers
    )
    assert response.status_code == 200, response.text
    return response.json()["conversation"]


def _post_message(client, csrf_headers, conversation_id, text, item_ids=None):
    body = {"text": text}
    if item_ids is not None:
        body["item_ids"] = item_ids
    return client.post(
        f"/api/ai/chat/conversations/{conversation_id}/messages",
        json=body,
        headers=csrf_headers,
    )


def _assert_ok(response):
    assert response.status_code == 200, response.text
    return response


# ============================================================================
# 1. Plain answer
# ============================================================================

def test_plain_answer_streams_tokens_and_persists(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("Hello!")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "Hi there"
    )
    _assert_ok(response)
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    tokens = "".join(p["text"] for p in _events_by_name(events, "token"))
    assert tokens == "Hello!"
    assert events[-1][0] == "turn_done"
    turn_done = events[-1][1]
    assert turn_done["stop_reason"] == "end_turn"
    assert turn_done["usage"] == {"input_tokens": 10, "output_tokens": 5}

    # Stored history: user + assistant messages.
    repo = AiChatRepository(db_connection)
    messages = repo.get_messages(conversation["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["parts"][0]["text"] == "Hi there"
    assert messages[1]["parts"][0]["text"] == "Hello!"

    # First message becomes the title (truncated to 60 chars).
    stored = repo.get_conversation(conversation["id"], test_user["id"])
    assert stored["title"] == "Hi there"

    # The user slot must be released after the turn.
    assert not ai_chat_service_module._active_users

    # The LLM saw the system prompt, tool specs and a user turn.
    request = fake.requests[0]
    assert request.system_prompt and "Synth Gallery" in request.system_prompt
    assert request.tools, "tool specs must be advertised"
    assert [turn.role for turn in request.turns] == ["user"]


def test_conversation_title_truncated_to_60_chars(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    long_text = "x" * 200

    response = _post_message(
        authenticated_client, csrf, conversation["id"], long_text
    )
    assert response.status_code == 200

    stored = AiChatRepository(db_connection).get_conversation(
        conversation["id"], test_user["id"]
    )
    assert stored["title"] == long_text[:60]
    assert len(stored["title"]) == 60


# ============================================================================
# 2. Tool flow
# ============================================================================

def test_tool_flow_feeds_result_back_and_persists(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(
        [
            [
                ToolCallsReady([ToolCall("c1", "list_folders", "{}")], ""),
                TurnComplete(StopReason.TOOL_USE),
            ],
            _text_turn("done", in_tokens=3, out_tokens=2),
        ]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "list folders"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    starts = _events_by_name(events, "tool_start")
    assert starts == [
        {"call_id": "c1", "name": "list_folders", "arguments": {}}
    ]
    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["call_id"] == "c1"
    assert results[0]["is_error"] is False
    assert events[-1][0] == "turn_done"
    assert events[-1][1]["usage"] == {"input_tokens": 3, "output_tokens": 2}

    # The second LLM call received the tool result for c1.
    second = fake.requests[1]
    tool_parts = [
        part
        for turn in second.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert len(tool_parts) == 1
    assert tool_parts[0].call_id == "c1"
    assert tool_parts[0].is_error is False
    assert json.loads(tool_parts[0].content) == {"folders": []}

    # Persisted history: user, assistant(tool_call), tool, assistant(text).
    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant",
    ]
    call_part = messages[1]["parts"][0]
    assert call_part["type"] == "tool_call"
    assert call_part["name"] == "list_folders"
    result_part = messages[2]["parts"][0]
    assert result_part["type"] == "tool_result"
    assert result_part["is_error"] is False


def test_unknown_tool_and_bad_json_become_error_results(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(
        [
            [
                ToolCallsReady(
                    [
                        ToolCall("c1", "does_not_exist", "{}"),
                        ToolCall("c2", "get_item", "{not json"),
                    ],
                    "",
                ),
                TurnComplete(StopReason.TOOL_USE),
            ],
            _text_turn("recovered"),
        ]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "go"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    results = _events_by_name(events, "tool_result")
    assert [r["is_error"] for r in results] == [True, True]
    assert "unknown tool" in results[0]["summary"]
    assert "invalid arguments JSON" in results[1]["summary"]

    second = fake.requests[1]
    tool_parts = [
        part
        for turn in second.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert [part.is_error for part in tool_parts] == [True, True]


# ============================================================================
# 3. / 4. Vision approve / deny
# ============================================================================

def _vision_script(item_id):
    return [
        [
            ToolCallsReady(
                [
                    ToolCall(
                        "v1",
                        "view_images",
                        json.dumps({"item_ids": [item_id], "reason": "tag"}),
                    )
                ],
                "",
            ),
            TurnComplete(StopReason.TOOL_USE),
        ],
        _text_turn("I see it", in_tokens=5, out_tokens=2),
    ]


def _run_vision_pause(authenticated_client, csrf, db_connection, test_user,
                      dek, monkeypatch, uploaded_photo):
    """Start a turn whose only tool call is view_images; returns
    (conversation, request_id, events)."""
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(_vision_script(uploaded_photo["id"]))
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "tag this photo"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    requests_received = _events_by_name(events, "vision_request")
    assert len(requests_received) == 1
    payload = requests_received[0]
    # The API's "filename" key holds the storage id; the stored title is
    # the original upload name.
    assert payload["items"] == [
        {"id": uploaded_photo["id"], "title": "test.jpg"}
    ]
    # No tool_result / turn_done: the turn is paused.
    assert _events_by_name(events, "tool_result") == []
    assert _events_by_name(events, "turn_done") == []

    # The dangling assistant tool_call is stored, awaiting the decision.
    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["parts"][0]["type"] == "tool_call"

    return conversation, payload["request_id"], fake


def test_vision_approved_attaches_images(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    conversation, request_id, fake = _run_vision_pause(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, uploaded_photo,
    )

    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    tokens = "".join(p["text"] for p in _events_by_name(events, "token"))
    assert tokens == "I see it"
    assert events[-1][0] == "turn_done"

    # The tool message was persisted with the image ids.
    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant",
    ]
    tool_part = messages[2]["parts"][0]
    assert tool_part["images"] == [uploaded_photo["id"]]
    assert tool_part["is_error"] is False
    # The content must bind the attached image to its item id so the model
    # cannot mix up which image belongs to which item.
    assert tool_part["content"].startswith("User approved. 1 image(s) attached.")
    assert uploaded_photo["id"] in tool_part["content"]

    # The follow-up LLM call carried the decrypted image as an ImagePart.
    resumed = fake.requests[1]
    tool_parts = [
        part
        for turn in resumed.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert len(tool_parts) == 1
    assert len(tool_parts[0].images) == 1
    image = tool_parts[0].images[0]
    assert isinstance(image, ImagePart)
    assert image.mime_type == "image/jpeg"
    raw = base64.b64decode(image.data_b64)
    assert raw[:2] == b"\xff\xd8", "hydrated image must be JPEG bytes"

    assert not ai_chat_service_module._active_users
    assert not ai_chat_service_module._pending_vision


def test_vision_denied_continues_without_images(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    conversation, request_id, fake = _run_vision_pause(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, uploaded_photo,
    )

    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/{request_id}/decision",
        json={"approved": False},
        headers=csrf,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1][0] == "turn_done"

    resumed = fake.requests[1]
    tool_parts = [
        part
        for turn in resumed.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert len(tool_parts) == 1
    assert tool_parts[0].images == []
    assert "denied" in tool_parts[0].content.lower()

    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    tool_part = messages[2]["parts"][0]
    assert tool_part["images"] == []


def test_vision_decision_unknown_request_404_and_expired_410(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    conversation, request_id, fake = _run_vision_pause(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, uploaded_photo,
    )

    # Unknown request id -> 404.
    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/nope/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 404

    # Expired request -> 410.
    pending = ai_chat_service_module._pending_vision[request_id]
    pending.created_at -= (
        AiChatService.VISION_TTL_SECONDS + 10
    )
    response = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert response.status_code == 410

    # Expired pending vision request with a NEW message: the dangling
    # assistant tool_call is replayed with a synthesized error result.
    service = build_ai_chat_service(db_connection)
    turns = service._build_llm_turns(conversation["id"])
    tool_turns = [t for t in turns if t.role == "tool"]
    assert tool_turns
    assert any(
        isinstance(part, ToolResult) and part.is_error
        for part in tool_turns[-1].parts
    )
    # Pending vision request for that call id: already purged.
    assert service.get_pending(conversation["id"]) is None


def test_vision_request_not_found_error_class():
    """Unknown vision requests surface the dedicated error class."""
    assert issubclass(VisionRequestNotFoundError, Exception)


# ============================================================================
# 5. Iteration cap
# ============================================================================

def test_tool_iteration_limit(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    script = [
        [
            ToolCallsReady(
                [ToolCall(f"c{i}", "list_folders", "{}")], ""
            ),
            TurnComplete(StopReason.TOOL_USE),
        ]
        for i in range(30)
    ]
    fake = FakeLLMClient(script)
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "loop forever"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    errors = _events_by_name(events, "error")
    assert errors[-1]["message"] == "tool iteration limit reached"
    assert _events_by_name(events, "turn_done") == []
    assert len(fake.requests) == AiChatService.MAX_TOOL_ITERATIONS

    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    last = messages[-1]
    assert last["role"] == "assistant"
    assert last["parts"][0]["text"] == "(stopped after tool iteration limit)"


# ============================================================================
# 6. / 7. Busy, missing DEK, provider not configured
# ============================================================================

def test_busy_user_gets_409(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    monkeypatch.setattr(
        ai_chat_service_module, "_active_users", {test_user["id"]}
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "hello"
    )
    assert response.status_code == 409


def test_missing_dek_gets_403(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    conversation = _create_conversation(authenticated_client, csrf)

    # The AuthMiddleware would restore the DEK from session storage, so
    # simulate "no key available" at the cache level for this request.
    monkeypatch.setattr(dek_cache, "get", lambda user_id: None)
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "hello"
    )
    assert response.status_code == 403
    assert "Encryption key not available" in response.json()["detail"]


def test_provider_not_configured_gets_400(
    authenticated_client, csrf, db_connection, test_user, dek
):
    # DEK present, but no provider/model configured.
    conversation = _create_conversation(authenticated_client, csrf)
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "hello"
    )
    assert response.status_code == 400
    assert "provider is not configured" in response.json()["detail"]


# ============================================================================
# 8. Ownership
# ============================================================================

def test_other_user_cannot_access_conversation(
    authenticated_client, csrf, db_connection, test_user, second_user, dek
):
    conversation = _create_conversation(authenticated_client, csrf)

    with login_as(
        authenticated_client, second_user["username"], second_user["password"]
    ):
        other_csrf = {
            "X-CSRF-Token": authenticated_client.cookies.get(
                CSRF_COOKIE_NAME, ""
            )
        }
        got = authenticated_client.get(
            f"/api/ai/chat/conversations/{conversation['id']}/messages"
        )
        assert got.status_code == 404
        posted = authenticated_client.post(
            f"/api/ai/chat/conversations/{conversation['id']}/messages",
            json={"text": "sneak"},
            headers=other_csrf,
        )
        assert posted.status_code == 404
        deleted = authenticated_client.delete(
            f"/api/ai/chat/conversations/{conversation['id']}",
            headers=other_csrf,
        )
        assert deleted.status_code == 404

    # The owner still sees the conversation.
    got = authenticated_client.get(
        f"/api/ai/chat/conversations/{conversation['id']}/messages"
    )
    assert got.status_code == 200


def test_foreign_item_in_view_images_is_error(
    authenticated_client, csrf, db_connection, test_user, second_user, dek,
    monkeypatch,
):
    _configure_provider(db_connection, test_user["id"], dek)

    other_folder = FolderRepository(db_connection).create(
        "Other Folder", second_user["id"]
    )
    foreign_id = ItemRepository(db_connection).create(
        item_type="media",
        folder_id=other_folder,
        user_id=second_user["id"],
        title="foreign.jpg",
    )
    ItemMediaRepository(db_connection).create(
        item_id=foreign_id, media_type="image", content_type="image/jpeg"
    )

    fake = FakeLLMClient(_vision_script(foreign_id))
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "look at this"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    # The tool errored, so no vision request was raised; the LLM gets the
    # error result and finishes the turn normally.
    assert _events_by_name(events, "vision_request") == []
    results = _events_by_name(events, "tool_result")
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "item not found" in results[0]["summary"]
    assert events[-1][0] == "turn_done"

    second_call = fake.requests[1]
    tool_parts = [
        part
        for turn in second_call.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert tool_parts and tool_parts[0].is_error is True


# ============================================================================
# 9. Conversations CRUD
# ============================================================================

def test_conversations_crud_and_cascade(
    authenticated_client, csrf, db_connection, test_user
):
    repo = AiChatRepository(db_connection)
    conversation_a = _create_conversation(authenticated_client, csrf)
    conversation_b = _create_conversation(authenticated_client, csrf)
    repo.add_message(
        conversation_a["id"], "user", [{"type": "text", "text": "hi"}]
    )

    listed = authenticated_client.get("/api/ai/chat/conversations").json()[
        "conversations"
    ]
    assert {c["id"] for c in listed} == {conversation_a["id"], conversation_b["id"]}

    # Activity bumps updated_at: conversation_a sorts first afterwards.
    # (Fresh rows share the same second-granularity timestamp, so make the
    # ordering deterministic directly.)
    db_connection.execute(
        "UPDATE ai_conversations SET updated_at = '2030-01-01 00:00:00' "
        "WHERE id = ?",
        (conversation_a["id"],),
    )
    db_connection.commit()
    listed = authenticated_client.get("/api/ai/chat/conversations").json()[
        "conversations"
    ]
    assert listed[0]["id"] == conversation_a["id"]

    messages = authenticated_client.get(
        f"/api/ai/chat/conversations/{conversation_a['id']}/messages"
    ).json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"

    deleted = authenticated_client.delete(
        f"/api/ai/chat/conversations/{conversation_a['id']}", headers=csrf
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "ok"}

    assert repo.get_messages(conversation_a["id"]) == []
    gone = authenticated_client.get(
        f"/api/ai/chat/conversations/{conversation_a['id']}/messages"
    )
    assert gone.status_code == 404

    missing = authenticated_client.delete(
        f"/api/ai/chat/conversations/{conversation_a['id']}", headers=csrf
    )
    assert missing.status_code == 404


# ============================================================================
# 10. Partial text persisted on disconnect
# ============================================================================

def test_partial_text_persisted_when_generator_closed(
    db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(
        [[TextDelta("Hel"), TextDelta("lo world"),
          TurnComplete(StopReason.END_TURN, 4, 2)]]
    )
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    repo = AiChatRepository(db_connection)
    conversation = repo.create_conversation(test_user["id"])

    async def scenario():
        db = create_connection()
        try:
            service = AiChatService(
                chat_repo=AiChatRepository(db),
                item_repo=ItemRepository(db),
                item_media_repo=ItemMediaRepository(db),
                provider_service=AiProviderService(
                    provider_repo=AiProviderRepository(db),
                    model_repo=AiModelRepository(db),
                    settings_repo=AiChatSettingsRepository(db),
                ),
            )
            generator = service.run_message_turn(
                test_user["id"], dek, conversation["id"], "hi"
            )
            received = []
            async for event in generator:
                received.append(event)
                break  # disconnect after the first token
            await generator.aclose()
        finally:
            db.close()

    asyncio.run(scenario())

    messages = repo.get_messages(conversation["id"])
    assert messages[0]["role"] == "user"
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["parts"][0]["text"] == "Hel"
    # The user slot was released by the finally block.
    assert not ai_chat_service_module._active_users


# ============================================================================
# Tool registry sanity (tool-level, no LLM involved)
# ============================================================================

def test_tool_registry_has_expected_tools():
    from app.application.services.ai_tools import TOOL_REGISTRY

    expected = {
        "search_items", "list_folder", "get_item", "list_tags",
        "get_tag_info", "list_folders", "list_albums", "get_tag_suggestions",
        "audit_library", "add_item_tags", "remove_item_tags", "create_tags",
        "update_item", "create_folder", "create_album", "add_items_to_album",
        "view_images",
    }
    assert set(TOOL_REGISTRY) == expected


def test_view_images_tool_rejects_notes(db_connection, test_user):
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )

    folder_id = FolderRepository(db_connection).create("F", test_user["id"])
    note_id = ItemRepository(db_connection).create(
        item_type="note",
        folder_id=folder_id,
        user_id=test_user["id"],
        title="note.txt",
    )
    executor = TOOL_REGISTRY["view_images"].executor
    with pytest.raises(ToolError) as excinfo:
        executor(
            {"item_ids": [note_id], "reason": "check"},
            ToolContext(user_id=test_user["id"]),
        )
    assert "notes have no images" in str(excinfo.value)


def test_create_tags_tool_creates_and_reports(
    db_connection, test_user, test_folder, general_category
):
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
    )

    executor = TOOL_REGISTRY["create_tags"].executor
    # "Not*Valid!" is invalid under the shared tag grammar ('*' is not an
    # allowed character); "Not Valid!" would normalize to a valid name.
    result = json.loads(executor(
        {"tag_names": ["sunset", "Not*Valid!", "sunset"]},
        ToolContext(user_id=test_user["id"]),
    ))
    assert result["created"] == ["sunset"]
    assert result["existing"] == []
    assert result["invalid"] == ["Not*Valid!"]

    # Second run reports the tag as existing.
    result = json.loads(executor(
        {"tag_names": ["sunset"]}, ToolContext(user_id=test_user["id"])
    ))
    assert result["created"] == []
    assert result["existing"] == ["sunset"]


def test_add_item_tags_tool_unknown_tag_errors(
    db_connection, test_user, test_folder
):
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )

    item_id = ItemRepository(db_connection).create(
        item_type="media",
        folder_id=test_folder,
        user_id=test_user["id"],
        title="img.jpg",
    )
    executor = TOOL_REGISTRY["add_item_tags"].executor
    with pytest.raises(ToolError) as excinfo:
        executor(
            {"item_id": item_id, "tag_names": ["does_not_exist_tag"]},
            ToolContext(user_id=test_user["id"]),
        )
    assert "create_tags" in str(excinfo.value).lower()


# ============================================================================
# audit_library tool (tool-level, no LLM involved)
# ============================================================================

def _seed_audit_item(
    db, folder_id, user_id, title, uploaded_at, *, item_type="media",
    width=100, height=100, thumb_width=280, tag_names=None,
):
    """Seed one item for audit tests; returns the item id."""
    item_id = ItemRepository(db).create(
        item_type=item_type,
        folder_id=folder_id,
        user_id=user_id,
        title=title,
        uploaded_at=uploaded_at,
    )
    if item_type == "media":
        ItemMediaRepository(db).create(
            item_id,
            media_type="image",
            original_name=f"{title or 'item'}.jpg",
            content_type="image/jpeg",
            width=width,
            height=height,
            thumb_width=thumb_width,
            thumb_height=100 if thumb_width else None,
        )
    if tag_names:
        from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

        ctx = ToolContext(user_id=user_id)
        TOOL_REGISTRY["create_tags"].executor({"tag_names": tag_names}, ctx)
        TOOL_REGISTRY["add_item_tags"].executor(
            {"item_id": item_id, "tag_names": tag_names}, ctx
        )
    return item_id


def test_audit_library_tool_reports_all_problem_kinds(
    db_connection, test_user, test_folder, general_category
):
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

    user_id = test_user["id"]
    base = datetime(2026, 10, 1, 12, 0, 0)

    clean = _seed_audit_item(
        db_connection, test_folder, user_id, "clean.jpg", base,
        tag_names=["sunset"],
    )
    untagged = _seed_audit_item(
        db_connection, test_folder, user_id, "untagged.jpg",
        base + timedelta(minutes=1),
    )
    no_thumb = _seed_audit_item(
        db_connection, test_folder, user_id, "nothumb.jpg",
        base + timedelta(minutes=2),
        thumb_width=None, tag_names=["sunset"],
    )
    no_dims = _seed_audit_item(
        db_connection, test_folder, user_id, "nodims.jpg",
        base + timedelta(minutes=3),
        width=None, height=None, tag_names=["sunset"],
    )
    untagged_note = _seed_audit_item(
        db_connection, test_folder, user_id, "note.txt",
        base + timedelta(minutes=4),
        item_type="note",
    )
    # Untitled AND untagged: one item can carry several problems at once.
    untitled = _seed_audit_item(
        db_connection, test_folder, user_id, None,
        base + timedelta(minutes=5),
    )

    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {}, ToolContext(user_id=user_id)
    ))

    assert result["total"] == 5
    assert result["summary"] == {
        "no_tags": 3, "untitled": 1, "no_thumbnail": 1, "no_dimensions": 1,
    }
    assert set(result["check_meanings"]) == set(result["summary"])
    by_id = {item["id"]: item for item in result["items"]}
    assert clean not in by_id
    assert by_id[untagged]["problems"] == ["no_tags"]
    assert by_id[no_thumb]["problems"] == ["no_thumbnail"]
    assert by_id[no_dims]["problems"] == ["no_dimensions"]
    assert by_id[untagged_note]["problems"] == ["no_tags"]
    assert by_id[untitled]["problems"] == ["no_tags", "untitled"]
    # Newest first, deterministic order for stable paging.
    assert [item["id"] for item in result["items"]] == [
        untitled, untagged_note, no_dims, no_thumb, untagged,
    ]
    assert result["truncated"] is False


def test_audit_library_tool_check_filter_and_unknown_check(
    db_connection, test_user, test_folder, general_category
):
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )

    user_id = test_user["id"]
    base = datetime(2026, 10, 1, 12, 0, 0)
    _seed_audit_item(
        db_connection, test_folder, user_id, "nothumb.jpg", base,
        thumb_width=None, tag_names=["sunset"],
    )
    untagged = _seed_audit_item(
        db_connection, test_folder, user_id, "untagged.jpg",
        base + timedelta(minutes=1),
    )

    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {"checks": ["no_tags"]}, ToolContext(user_id=user_id)
    ))
    assert result["checks"] == ["no_tags"]
    assert result["summary"] == {"no_tags": 1}
    assert [item["id"] for item in result["items"]] == [untagged]

    with pytest.raises(ToolError) as excinfo:
        TOOL_REGISTRY["audit_library"].executor(
            {"checks": ["bogus"]}, ToolContext(user_id=user_id)
        )
    assert "no_tags" in str(excinfo.value)


def test_audit_library_tool_scopes_to_folder_subtree(
    db_connection, test_user, test_folder
):
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )

    user_id = test_user["id"]
    db = db_connection
    sub = FolderRepository(db).create("Sub", user_id, parent_id=test_folder)
    other = FolderRepository(db).create("Other", user_id)
    base = datetime(2026, 10, 1, 12, 0, 0)

    in_folder = _seed_audit_item(db, test_folder, user_id, "a.jpg", base)
    in_sub = _seed_audit_item(
        db, sub, user_id, "b.jpg", base + timedelta(minutes=1)
    )
    _seed_audit_item(db, other, user_id, "c.jpg", base + timedelta(minutes=2))

    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {"folder_id": test_folder}, ToolContext(user_id=user_id)
    ))
    assert result["folder_id"] == test_folder
    assert result["total"] == 2
    assert sorted(i["id"] for i in result["items"]) == sorted(
        [in_folder, in_sub]
    )

    # A foreign folder id must be rejected, not audited.
    foreign_user_id = UserRepository(db).create(
        "otheruser", "OtherPass123!", "Other"
    )
    foreign_folder = FolderRepository(db).create("Foreign", foreign_user_id)
    with pytest.raises(ToolError):
        TOOL_REGISTRY["audit_library"].executor(
            {"folder_id": foreign_folder}, ToolContext(user_id=user_id)
        )


def test_audit_library_tool_pagination(db_connection, test_user, test_folder):
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

    user_id = test_user["id"]
    base = datetime(2026, 10, 1, 12, 0, 0)
    ids = [
        _seed_audit_item(
            db_connection, test_folder, user_id, f"item{i}.jpg",
            base + timedelta(minutes=i),
        )
        for i in range(5)
    ]
    executor = TOOL_REGISTRY["audit_library"].executor
    ctx = ToolContext(user_id=user_id)

    pages = [
        json.loads(executor({"limit": 2, "offset": offset}, ctx))
        for offset in (0, 2, 4)
    ]
    assert all(page["total"] == 5 for page in pages)
    assert [page["truncated"] for page in pages] == [True, True, False]
    seen = [item["id"] for page in pages for item in page["items"]]
    assert seen == ids[::-1]
