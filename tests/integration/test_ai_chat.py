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
    AlbumRepository,
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
    TextPart,
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


def _post_message(
    client, csrf_headers, conversation_id, text, item_ids=None, album_ids=None
):
    body = {"text": text}
    if item_ids is not None:
        body["item_ids"] = item_ids
    if album_ids is not None:
        body["album_ids"] = album_ids
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


# ============================================================================
# 1b. Album attachment expansion
# ============================================================================


def test_album_ids_expand_into_context_part(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    test_folder, csrf_token,
):
    """Album ids attached to a message are expanded into their member item
    ids and stored as a context part alongside the user text."""
    _configure_provider(db_connection, test_user["id"], dek)

    # Upload two photos into the test folder.
    from PIL import Image
    import io

    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="JPEG", quality=85)
    raw = img_bytes.getvalue()

    response = authenticated_client.post(
        "/api/uploads",
        data={"folder_id": test_folder},
        files={"file": ("a.jpg", raw, "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    photo_a = response.json()["id"]

    response = authenticated_client.post(
        "/api/uploads",
        data={"folder_id": test_folder},
        files={"file": ("b.jpg", raw, "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    photo_b = response.json()["id"]

    # Upload a third photo (will be passed via item_ids).
    response = authenticated_client.post(
        "/api/uploads",
        data={"folder_id": test_folder},
        files={"file": ("c.jpg", raw, "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    photo_c = response.json()["id"]

    # Create an album with photos a and b.
    album_id = AlbumRepository(db_connection).create(test_folder, test_user["id"], "Pics")
    AlbumRepository(db_connection).add_item(album_id, photo_a, position=1)
    AlbumRepository(db_connection).add_item(album_id, photo_b, position=2)

    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client,
        csrf,
        conversation["id"],
        "look at these",
        item_ids=[photo_c],
        album_ids=[album_id],
    )
    assert response.status_code == 200

    # Verify the persisted user message carries the deduped union.
    repo = AiChatRepository(db_connection)
    messages = repo.get_messages(conversation["id"])
    parts = messages[0]["parts"]
    text_part = [p for p in parts if p["type"] == "text"][0]
    assert text_part["text"] == "look at these"
    ctx_part = [p for p in parts if p["type"] == "context"][0]
    assert ctx_part["item_ids"] == [photo_c, photo_a, photo_b]


def test_unknown_album_id_rejected_before_stream(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
):
    """An unknown album id causes a 404 before SSE streaming starts."""
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _post_message(
        authenticated_client,
        csrf,
        conversation["id"],
        "hello",
        album_ids=["00000000-0000-0000-0000-000000000000"],
    )
    assert response.status_code == 404
    assert "Album not found" in response.json()["detail"]

    # No user message was persisted.
    repo = AiChatRepository(db_connection)
    messages = repo.get_messages(conversation["id"])
    assert len([m for m in messages if m["role"] == "user"]) == 0


def test_max_output_tokens_reaches_llm_request(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    """The active model's max_output_tokens is sent as max_tokens; without
    one the field stays None (providers keep their defaults)."""
    provider_id = _configure_provider(db_connection, test_user["id"], dek)
    AiModelRepository(db_connection).replace_for_provider(
        provider_id,
        [
            {
                "model_id": "test-model",
                "display_name": "Test Model",
                "supports_tools": 1,
                "supports_vision": 1,
                "max_output_tokens": 4096,
            }
        ],
    )
    fake = FakeLLMClient([_text_turn("Hello!")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    _assert_ok(
        _post_message(authenticated_client, csrf, conversation["id"], "Hi")
    )
    assert fake.requests[0].max_tokens == 4096

    # Without a stored limit the request must not carry max_tokens.
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
    fake = FakeLLMClient([_text_turn("Hello again!")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation2 = _create_conversation(authenticated_client, csrf)
    _assert_ok(
        _post_message(authenticated_client, csrf, conversation2["id"], "Hi")
    )
    assert fake.requests[0].max_tokens is None


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

    # The view_images result is streamed immediately: the client's tool
    # card must not stay "Running..." until the post-turn refetch.
    results = _events_by_name(events, "tool_result")
    assert results and results[0]["call_id"] == "v1"
    assert results[0]["name"] == "view_images"
    assert results[0]["is_error"] is False
    assert events[0][0] == "tool_result"

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
    # The denial result is streamed to the client immediately as well.
    results = _events_by_name(events, "tool_result")
    assert results and results[0]["call_id"] == "v1"
    assert events[0][0] == "tool_result"
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


def test_vision_pause_keeps_remaining_batch_calls(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    """A batch like [view_images, get_item] must not lose its tail: the
    calls after the pausing view_images run after the decision."""
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([
        [
            ToolCallsReady(
                [
                    ToolCall(
                        "v1", "view_images",
                        json.dumps({
                            "item_ids": [uploaded_photo["id"]],
                            "reason": "tag",
                        }),
                    ),
                    ToolCall("g1", "get_item", json.dumps(
                        {"item_id": uploaded_photo["id"]}
                    )),
                ],
                "",
            ),
            TurnComplete(StopReason.TOOL_USE),
        ],
        _text_turn("done"),
    ])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    response = _post_message(
        authenticated_client, csrf, conversation["id"], "look and fetch"
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    # The pause happens at view_images; get_item has not run yet.
    assert len(_events_by_name(events, "vision_request")) == 1
    assert _events_by_name(events, "tool_result") == []

    approve = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/{_events_by_name(events, 'vision_request')[0]['request_id']}"
        f"/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert approve.status_code == 200
    resumed = _parse_sse(approve.text)

    starts = _events_by_name(resumed, "tool_start")
    assert [s["call_id"] for s in starts] == ["g1"]
    results = _events_by_name(resumed, "tool_result")
    assert [r["call_id"] for r in results] == ["v1", "g1"]
    assert _events_by_name(events, "turn_done") == []
    assert resumed[-1][0] == "turn_done"

    messages = AiChatRepository(db_connection).get_messages(conversation["id"])
    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "tool", "assistant",
    ]


def test_get_messages_returns_pending_decisions(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    """GET /messages exposes the paused vision request so the frontend can
    restore the approval card after a reload or chat switch."""
    conversation, request_id, _ = _run_vision_pause(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, uploaded_photo,
    )

    response = authenticated_client.get(
        f"/api/ai/chat/conversations/{conversation['id']}/messages",
        headers=csrf,
    )
    assert response.status_code == 200
    data = response.json()
    pending = data["pending_vision"]
    assert pending["request_id"] == request_id
    assert pending["call_id"] == "v1"
    assert pending["items"] == [
        {"id": uploaded_photo["id"], "title": "test.jpg"}
    ]
    assert data["pending_tool_confirm"] is None

    # Resolving the request clears it from the endpoint too.
    approved = authenticated_client.post(
        f"/api/ai/chat/conversations/{conversation['id']}"
        f"/vision/{request_id}/decision",
        json={"approved": True},
        headers=csrf,
    )
    assert approved.status_code == 200
    data = authenticated_client.get(
        f"/api/ai/chat/conversations/{conversation['id']}/messages",
        headers=csrf,
    ).json()
    assert data["pending_vision"] is None


def test_new_message_drops_pending_vision(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch,
    uploaded_photo,
):
    """A fresh user message supersedes a paused turn: its pending vision
    request is dropped and the dangling call replays with an error."""
    conversation, request_id, fake = _run_vision_pause(
        authenticated_client, csrf, db_connection, test_user, dek,
        monkeypatch, uploaded_photo,
    )
    fake.script = [_text_turn("starting over")]

    response = _post_message(
        authenticated_client, csrf, conversation["id"], "forget it"
    )
    assert response.status_code == 200

    assert not ai_chat_service_module._pending_vision
    # The replay answered the dangling view_images call with an error so
    # the provider turn stays valid.
    replay = fake.requests[-1]
    tool_parts = [
        part
        for turn in replay.turns
        if turn.role == "tool"
        for part in turn.parts
        if isinstance(part, ToolResult)
    ]
    assert any(part.is_error for part in tool_parts)
    # A normal message chains onto the active leaf — the replay carries
    # the whole prior branch, not just the new message.
    user_turns = [
        turn for turn in replay.turns if turn.role == "user"
    ]
    assert ["".join(p.text for p in turn.parts if isinstance(p, TextPart))
            for turn in user_turns] == ["tag this photo", "forget it"]


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
        for i in range(AiChatService.MAX_TOOL_ITERATIONS + 1)
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
        "search_items", "list_folder", "get_item", "get_item_metadata",
        "grep_library", "read_note", "list_tags",
        "get_tag_info", "list_folders", "list_albums", "list_album_items",
        "get_tag_suggestions", "record_tag_feedback",
        "audit_library", "add_item_tags", "remove_item_tags", "create_tags",
        "update_item", "set_item_metadata", "set_tag_descriptions",
        "create_note", "write_note", "edit_note", "create_folder",
        "create_album", "add_items_to_album",
        "view_images",
        "fetch_url", "web_search",
        "read_memory", "create_memory_section", "write_memory",
        "edit_memory", "delete_memory_section", "set_memory_summary",
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
    # The error must not push the model towards creating the tag: missing
    # tags are reported to the user instead.
    message = str(excinfo.value)
    assert "unknown tags" in message.lower()
    assert "create_tags" not in message.lower()


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
    assert clean[:12] not in by_id
    assert by_id[untagged[:12]]["problems"] == ["no_tags"]
    assert by_id[no_thumb[:12]]["problems"] == ["no_thumbnail"]
    assert by_id[no_dims[:12]]["problems"] == ["no_dimensions"]
    assert by_id[untagged_note[:12]]["problems"] == ["no_tags"]
    assert by_id[untitled[:12]]["problems"] == ["no_tags", "untitled"]
    # Newest first, deterministic order for stable paging; ids are short.
    assert [item["id"] for item in result["items"]] == [
        i[:12] for i in (untitled, untagged_note, no_dims, no_thumb, untagged)
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
    assert [item["id"] for item in result["items"]] == [untagged[:12]]

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
    assert result["folder_id"] == test_folder[:12]
    assert result["total"] == 2
    assert sorted(i["id"] for i in result["items"]) == sorted(
        [in_folder[:12], in_sub[:12]]
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
    assert seen == [i[:12] for i in ids[::-1]]


def test_audit_library_tool_can_skip_subfolders(
    db_connection, test_user, test_folder
):
    """include_subfolders=false audits the folder itself only."""
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

    user_id = test_user["id"]
    db = db_connection
    sub = FolderRepository(db).create("Sub", user_id, parent_id=test_folder)
    base = datetime(2026, 10, 1, 12, 0, 0)

    in_folder = _seed_audit_item(db, test_folder, user_id, "a.jpg", base)
    in_sub = _seed_audit_item(
        db, sub, user_id, "b.jpg", base + timedelta(minutes=1)
    )

    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {"folder_id": test_folder, "include_subfolders": False},
        ToolContext(user_id=user_id),
    ))
    assert [item["id"] for item in result["items"]] == [in_folder[:12]]
    assert result["total"] == 1

    # Default stays subtree-wide.
    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {"folder_id": test_folder}, ToolContext(user_id=user_id)
    ))
    assert sorted(i["id"] for i in result["items"]) == sorted(
        [in_folder[:12], in_sub[:12]]
    )


def test_audit_library_tool_includes_album_items(
    db_connection, test_user, test_folder
):
    """Album membership must not hide items from the audit: albums are a
    junction table, items keep their folder_id."""
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext
    from app.infrastructure.repositories import AlbumRepository

    user_id = test_user["id"]
    db = db_connection
    item_id = _seed_audit_item(
        db, test_folder, user_id, "albummed.jpg", datetime(2026, 10, 1, 12)
    )
    album_id = AlbumRepository(db).create(test_folder, user_id, "Pics")
    AlbumRepository(db).add_item(album_id, item_id)

    result = json.loads(TOOL_REGISTRY["audit_library"].executor(
        {"folder_id": test_folder}, ToolContext(user_id=user_id)
    ))
    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == [item_id[:12]]


# ============================================================================
# list_album_items tool (tool-level, no LLM involved)
# ============================================================================

def _seed_album_with_items(db, folder_id, user_id, titles):
    """Create an album and one media item per title; returns (album_id, ids)."""
    from app.infrastructure.repositories import AlbumRepository

    album_id = AlbumRepository(db).create(folder_id, user_id, "Pics")
    ids = []
    for index, title in enumerate(titles, start=1):
        item_id = _seed_audit_item(
            db, folder_id, user_id, title, datetime(2026, 10, 1, 12, index)
        )
        AlbumRepository(db).add_item(album_id, item_id, position=index)
        ids.append(item_id)
    return album_id, ids


def test_list_album_items_tool_pages_items(db_connection, test_user, test_folder):
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

    user_id = test_user["id"]
    album_id, ids = _seed_album_with_items(
        db_connection, test_folder, user_id,
        ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"],
    )
    executor = TOOL_REGISTRY["list_album_items"].executor
    ctx = ToolContext(user_id=user_id)

    first = json.loads(executor({"album_id": album_id, "limit": 2}, ctx))
    assert first["album"]["id"] == album_id[:12]
    assert first["album"]["item_count"] == 5
    assert first["total"] == 5
    assert [i["id"] for i in first["items"]] == [i[:12] for i in ids[:2]]
    assert [i["position"] for i in first["items"]] == [1, 2]
    assert first["next_offset"] == 2

    second = json.loads(executor(
        {"album_id": album_id, "limit": 2, "offset": 2}, ctx
    ))
    assert [i["id"] for i in second["items"]] == [i[:12] for i in ids[2:4]]

    tail = json.loads(executor(
        {"album_id": album_id, "limit": 2, "offset": 4}, ctx
    ))
    assert [i["id"] for i in tail["items"]] == [i[:12] for i in ids[4:]]
    assert "next_offset" not in tail


def test_list_album_items_tool_access_and_unknown(db_connection, test_user):
    """Albums of folders without permission are invisible; unknown id errors."""
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )
    from app.infrastructure.repositories import (
        AlbumRepository,
        FolderRepository,
        UserRepository,
    )

    user_id = test_user["id"]
    other_user_id = UserRepository(db_connection).create(
        "otheruser", "OtherPass123!", "Other"
    )
    foreign_folder = FolderRepository(db_connection).create(
        "Foreign", other_user_id
    )
    album_id, _ = _seed_album_with_items(
        db_connection, foreign_folder, other_user_id, ["x.jpg"]
    )
    executor = TOOL_REGISTRY["list_album_items"].executor
    ctx = ToolContext(user_id=user_id)

    with pytest.raises(ToolError):
        executor({"album_id": album_id}, ctx)
    with pytest.raises(ToolError):
        executor({"album_id": "00000000-0000-0000-0000-000000000000"}, ctx)


# ============================================================================
# Short id prefix resolution (tool-level, no LLM involved)
# ============================================================================

def test_tool_short_id_prefix_resolution(db_connection, test_user, test_folder):
    """Full ids and unique 12-char prefixes resolve; ambiguity is an error."""
    from app.application.services.ai_tools import (
        TOOL_REGISTRY,
        ToolContext,
        ToolError,
    )

    user_id = test_user["id"]
    db = db_connection
    item_id = _seed_audit_item(
        db, test_folder, user_id, "x.jpg", datetime(2026, 10, 1, 12)
    )
    # A second item crafted onto the same 12-char prefix: ambiguity bait.
    from app.infrastructure.repositories import ItemMediaRepository

    colliding_id = item_id[:12] + "0" * 24
    ItemRepository(db).create(
        item_type="media",
        folder_id=test_folder,
        user_id=user_id,
        item_id=colliding_id,
        title="y.jpg",
    )
    ItemMediaRepository(db).create(
        colliding_id,
        media_type="image",
        original_name="y.jpg",
        content_type="image/jpeg",
        width=100,
        height=100,
        thumb_width=280,
        thumb_height=100,
    )

    ctx = ToolContext(user_id=user_id)
    get_item = TOOL_REGISTRY["get_item"].executor

    # A full id is accepted as-is.
    assert json.loads(get_item({"item_id": item_id}, ctx))["id"] == item_id[:12]
    # A prefix long enough to be unique resolves (past the collision point).
    assert json.loads(
        get_item({"item_id": colliding_id[:16]}, ctx)
    )["id"] == colliding_id[:12]

    with pytest.raises(ToolError) as excinfo:
        get_item({"item_id": item_id[:12] + "zz"}, ctx)
    assert "not found" in str(excinfo.value)

    # The shared prefix is ambiguous: candidates are listed, nothing matches.
    with pytest.raises(ToolError) as excinfo:
        get_item({"item_id": item_id[:12]}, ctx)
    message = str(excinfo.value)
    assert "ambiguous" in message
    assert "Candidates" in message


# ============================================================================
# list_folder tool paging (tool-level, no LLM involved)
# ============================================================================

def test_list_folder_tool_pages_items(db_connection, test_user, test_folder):
    """offset/next_offset page the standalone items; output stays compact."""
    from app.application.services.ai_tools import TOOL_REGISTRY, ToolContext

    user_id = test_user["id"]
    db = db_connection
    ids = [
        _seed_audit_item(
            db, test_folder, user_id, f"f{i}.jpg",
            datetime(2026, 10, 1, 12, i),
        )
        for i in range(5)
    ]

    executor = TOOL_REGISTRY["list_folder"].executor
    ctx = ToolContext(user_id=user_id)

    first = json.loads(executor(
        {"folder_id": test_folder, "limit": 2}, ctx
    ))
    assert first["folder"]["id"] == test_folder[:12]
    assert first["total"] == 5
    assert [i["id"] for i in first["items"]] == [
        i[:12] for i in ids[::-1][:2]
    ]
    assert first["next_offset"] == 2
    # Compact shape: items are always media, so no per-item type field.
    assert all(set(i) == {"id", "title", "media_type"} for i in first["items"])

    tail = json.loads(executor(
        {"folder_id": test_folder, "limit": 3, "offset": 3}, ctx
    ))
    assert [i["id"] for i in tail["items"]] == [
        i[:12] for i in ids[::-1][3:]
    ]
    assert "next_offset" not in tail


# ============================================================================
# Message tree: edit/retry branches, version switch
# ============================================================================

def _edit_message(client, csrf_headers, conversation_id, text, edit_of):
    return client.post(
        f"/api/ai/chat/conversations/{conversation_id}/messages",
        json={"text": text, "edit_of_message_id": edit_of},
        headers=csrf_headers,
    )


def _switch_branch(client, csrf_headers, conversation_id, message_id):
    return client.post(
        f"/api/ai/chat/conversations/{conversation_id}/branch",
        json={"message_id": message_id},
        headers=csrf_headers,
    )


def test_edit_creates_sibling_branch_and_version_switch_works(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    """Retry/edit of an earlier user message forks a sibling branch; the
    version switch moves the conversation between the two branches."""
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([
        _text_turn("answer one"),
        _text_turn("answer two"),
    ])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    conv_id = conversation["id"]
    repo = AiChatRepository(db_connection)

    _assert_ok(_post_message(authenticated_client, csrf, conv_id, "question v1"))
    history = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]
    user_v1 = history[0]
    assert user_v1["role"] == "user"
    assert user_v1["parent_id"] is None
    assert user_v1["version_count"] == 1  # no siblings yet

    # Retry the same question: a sibling user message branches off the
    # same parent (None) and the active leaf moves to the new branch.
    _assert_ok(
        _edit_message(authenticated_client, csrf, conv_id, "question v2", user_v1["id"])
    )
    branch = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]
    assert [m["role"] for m in branch] == ["user", "assistant"]
    assert branch[0]["parts"][0]["text"] == "question v2"
    assert branch[1]["parts"][0]["text"] == "answer two"
    # Version metadata describes the whole group, not the active path.
    assert branch[0]["version"] == 2
    assert branch[0]["version_count"] == 2
    assert branch[0]["sibling_ids"] == [user_v1["id"], branch[0]["id"]]

    # The old branch is still stored.
    old_user = repo.get_message(conv_id, user_v1["id"])
    assert old_user is not None and old_user["role"] == "user"

    # Switch back to version 1: the path now shows the original branch.
    _assert_ok(
        _switch_branch(authenticated_client, csrf, conv_id, user_v1["id"])
    )
    restored = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]
    assert [m["parts"][0]["text"] for m in restored] == ["question v1", "answer one"]
    assert restored[0]["version"] == 1

    # ...and forward again to version 2.
    _assert_ok(
        _switch_branch(authenticated_client, csrf, conv_id, branch[0]["id"])
    )
    again = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]
    assert again[0]["parts"][0]["text"] == "question v2"

    # All four messages chained: new leaf = the newest assistant message.
    stored_conv = repo.get_conversation(conv_id, test_user["id"])
    assert stored_conv["active_leaf_id"] == again[1]["id"]


def test_edit_of_unknown_message_is_404(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)

    response = _edit_message(
        authenticated_client, csrf, conversation["id"], "x", 999999
    )
    assert response.status_code == 404
    # Foreign conversation id: 404 as well.
    response = _edit_message(
        authenticated_client, csrf, "no-such-conversation", "x", 1
    )
    assert response.status_code == 404


def test_switch_branch_validates_message_and_role(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    conv_id = conversation["id"]
    _assert_ok(_post_message(authenticated_client, csrf, conv_id, "Hi"))

    history = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]
    assistant_id = history[1]["id"]

    # Unknown message id.
    response = _switch_branch(authenticated_client, csrf, conv_id, 999999)
    assert response.status_code == 404
    # Assistant messages are not switch targets (only user versions).
    response = _switch_branch(authenticated_client, csrf, conv_id, assistant_id)
    assert response.status_code == 404


def test_switch_branch_is_rejected_while_busy(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    _configure_provider(db_connection, test_user["id"], dek)
    conversation = _create_conversation(authenticated_client, csrf)
    ai_chat_service_module._active_users.add(test_user["id"])
    try:
        response = _switch_branch(
            authenticated_client, csrf, conversation["id"], 1
        )
        assert response.status_code == 409
    finally:
        ai_chat_service_module._active_users.discard(test_user["id"])


def test_switch_branch_drops_pending_decisions(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    """Leaving the branch of a paused turn discards its pending vision and
    confirmation requests (resolving them would append to the wrong branch)."""
    from app.application.services.ai_chat_service import (
        ToolConfirmation,
        VisionRequest,
    )

    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient([_text_turn("ok")])
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    conv_id = conversation["id"]
    _assert_ok(_post_message(authenticated_client, csrf, conv_id, "Hi"))
    history = authenticated_client.get(
        f"/api/ai/chat/conversations/{conv_id}/messages"
    ).json()["messages"]

    vision = VisionRequest(
        request_id="req-v", user_id=test_user["id"], conversation_id=conv_id,
        tool_call_id="c1", item_ids=[], created_at=0.0,
    )
    confirmation = ToolConfirmation(
        request_id="req-c", user_id=test_user["id"], conversation_id=conv_id,
        call_id="c2", tool_name="update_item", arguments_json="{}",
        remaining_calls=[], created_at=0.0,
    )
    ai_chat_service_module._pending_vision["req-v"] = vision
    ai_chat_service_module._pending_confirmations["req-c"] = confirmation
    try:
        _assert_ok(
            _switch_branch(authenticated_client, csrf, conv_id, history[0]["id"])
        )
        assert "req-v" not in ai_chat_service_module._pending_vision
        assert "req-c" not in ai_chat_service_module._pending_confirmations
    finally:
        ai_chat_service_module._pending_vision.pop("req-v", None)
        ai_chat_service_module._pending_confirmations.pop("req-c", None)


def test_tool_turns_chain_onto_the_branch(
    authenticated_client, csrf, db_connection, test_user, dek, monkeypatch
):
    """A tool flow (user, assistant tool_call, tool result, assistant)
    keeps a single parent chain in the tree."""
    scripted = [
        [
            TextDelta("Let me look."),
            ToolCallsReady([ToolCall("c1", "list_folders", "{}")], ""),
        ],
        [TextDelta("Done."), TurnComplete(StopReason.END_TURN, 1, 1)],
    ]
    _configure_provider(db_connection, test_user["id"], dek)
    fake = FakeLLMClient(scripted)
    monkeypatch.setattr(
        ai_chat_service_module, "_get_llm_client", lambda protocol: fake
    )
    conversation = _create_conversation(authenticated_client, csrf)
    conv_id = conversation["id"]
    _assert_ok(_post_message(authenticated_client, csrf, conv_id, "Hi"))

    repo = AiChatRepository(db_connection)
    messages = repo.get_messages(conv_id)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    for prev, nxt in zip(messages, messages[1:]):
        assert nxt["parent_id"] == prev["id"]
