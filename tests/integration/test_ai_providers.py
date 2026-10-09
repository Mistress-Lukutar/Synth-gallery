"""Integration tests for per-user AI provider settings (migration 0009).

Covers the 0009 migration (new AI chat tables, legacy queue dropped),
the provider/model/settings repositories, the AiProviderService and the
/api/user/ai routes.

The global app fixture does not include this router, so each test builds
its own FastAPI app with a fake-auth middleware.
"""
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.ai_provider_service import AiProviderService
from app.infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)
from app.infrastructure.repositories.ai_model_repository import AiModelRepository
from app.infrastructure.repositories.ai_provider_repository import (
    AiProviderRepository,
)
from app.infrastructure.services.encryption import (
    EncryptionService,
    dek_cache,
)
from app.routes.ai_provider_settings import router

RAW_KEY = "sk-secret-1234567890"
MASKED_KEY = "sk-…7890"  # first 3 + ellipsis + last 4

NEW_TABLES = (
    "ai_providers",
    "ai_models",
    "ai_chat_settings",
    "ai_conversations",
    "ai_messages",
)
LEGACY_TABLES = ("ai_tagging_jobs", "ai_api_keys")


# The LLM client package is built in parallel; it may not be importable yet.
try:
    from app.infrastructure.services import llm as llm_pkg
except ImportError:  # pragma: no cover - depends on the parallel build
    llm_pkg = None


# ============================================================================
# Helpers / fixtures
# ============================================================================

def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _make_service(db) -> AiProviderService:
    return AiProviderService(
        provider_repo=AiProviderRepository(db),
        model_repo=AiModelRepository(db),
        settings_repo=AiChatSettingsRepository(db),
    )


def _create_provider(client, **overrides) -> dict:
    """POST a provider and return the response JSON."""
    payload = {
        "label": "Test Provider",
        "protocol": "openai_compatible",
        "base_url": "https://api.example.com/v1",
        "api_key": RAW_KEY,
    }
    payload.update(overrides)
    return client.post("/api/user/ai/providers", json=payload).json()


class _FetchedModel:
    """Attribute-style stand-in for the LLM package's ModelInfo."""

    def __init__(
        self,
        model_id,
        display_name,
        supports_tools=False,
        supports_vision=False,
        context_tokens=None,
        max_output_tokens=None,
        limits_source=None,
    ):
        self.model_id = model_id
        self.display_name = display_name
        self.supports_tools = supports_tools
        self.supports_vision = supports_vision
        self.context_tokens = context_tokens
        self.max_output_tokens = max_output_tokens
        self.limits_source = limits_source


class _StubError(Exception):
    """Stand-in for the LLM package's LLMError."""


class _StubClient:
    """Stand-in for an LLM protocol client."""

    def __init__(self, models=None, error=None):
        self.models = models or []
        self.error = error
        self.calls = []

    async def fetch_models(self, base_url, api_key):
        self.calls.append((base_url, api_key))
        if self.error is not None:
            raise self.error
        return list(self.models)


def _identity_protocol(value):
    return value


def _patch_llm(monkeypatch, client, error_cls):
    """Point the service's lazy LLM import at a stub client.

    Prefers patching ``app.infrastructure.services.llm.get_llm_client``;
    falls back to patching the service's ``_llm`` accessor when the
    package has not landed yet.
    """
    if llm_pkg is not None:
        monkeypatch.setattr(llm_pkg, "get_llm_client", lambda protocol: client)
    else:
        monkeypatch.setattr(
            AiProviderService,
            "_llm",
            staticmethod(
                lambda: (_identity_protocol, lambda protocol: client, error_cls)
            ),
        )


@pytest.fixture
def dek(test_user):
    """Generate a DEK for the test user and put it in the session cache."""
    key = EncryptionService.generate_dek()
    dek_cache.set(test_user["id"], key)
    yield key
    dek_cache.invalidate(test_user["id"])


@pytest.fixture
def ai_client(test_user, fresh_database):
    """TestClient for a minimal app exposing only the AI settings router."""
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def fake_auth(request, call_next):
        request.state.user = {
            "id": test_user["id"],
            "username": test_user["username"],
            "is_admin": False,
        }
        return await call_next(request)

    with TestClient(app) as test_client:
        yield test_client


# ============================================================================
# Migration 0009
# ============================================================================

def test_migration_creates_ai_chat_tables_and_drops_legacy_queue(fresh_database):
    """After upgrading to head the AI chat tables exist and the legacy
    tagging-queue tables are gone."""
    conn = sqlite3.connect(str(fresh_database))
    try:
        names = _table_names(conn)
        assert set(NEW_TABLES) <= names
        assert not (set(LEGACY_TABLES) & names)
    finally:
        conn.close()


def test_migration_downgrade_restores_legacy_schema(tmp_path, monkeypatch):
    """Downgrading to 0008 drops the chat tables and recreates the legacy
    queue tables (with their backfilled columns folded in); upgrading again
    reapplies 0009."""
    import app.database as db_module
    from app.config import BASE_DIR

    db_path = tmp_path / "migration.db"
    monkeypatch.setattr(db_module, "DATABASE_PATH", db_path)

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_module.MIGRATIONS_DIR))

    try:
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        names = _table_names(conn)
        conn.close()
        assert set(NEW_TABLES) <= names
        assert not (set(LEGACY_TABLES) & names)

        command.downgrade(cfg, "0008")

        conn = sqlite3.connect(db_path)
        names = _table_names(conn)
        job_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ai_tagging_jobs)")
        }
        key_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ai_api_keys)")
        }
        job_indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
                " AND tbl_name = 'ai_tagging_jobs'"
            )
        }
        conn.close()
        assert set(LEGACY_TABLES) <= names
        assert not (set(NEW_TABLES) & names)
        # ALTER-backfilled columns are folded into the CREATE.
        assert {"user_id", "processing_deadline"} <= job_columns
        assert {"rate_limit_tier", "last_used_at"} <= key_columns
        assert {
            "idx_ai_jobs_status",
            "idx_ai_jobs_item",
            "idx_ai_jobs_user",
            "idx_ai_jobs_deadline",
        } <= job_indexes

        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        names = _table_names(conn)
        conn.close()
        assert set(NEW_TABLES) <= names
        assert not (set(LEGACY_TABLES) & names)
    finally:
        # Release the engine's file handle so tmp_path cleanup works.
        db_module.get_engine().dispose()


# ============================================================================
# Provider CRUD via routes
# ============================================================================

def test_requires_dek_in_session_cache(ai_client):
    """Without a DEK in the cache the endpoints refuse to work."""
    response = ai_client.get("/api/user/ai/providers")
    assert response.status_code == 403
    assert "Encryption key not available" in response.json()["detail"]


def test_create_provider_masks_key(ai_client, dek):
    """Creating a provider returns a masked key and never the raw one."""
    response = ai_client.post(
        "/api/user/ai/providers",
        json={
            "label": "Test Provider",
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": RAW_KEY,
        },
    )
    assert response.status_code == 200
    provider = response.json()["provider"]
    assert provider["api_key_masked"] == MASKED_KEY
    assert provider["has_api_key"] is True
    assert provider["label"] == "Test Provider"
    assert provider["protocol"] == "openai_compatible"
    assert RAW_KEY not in response.text
    assert "api_key_encrypted" not in provider


def test_create_provider_rejects_bad_protocol(ai_client, dek):
    response = ai_client.post(
        "/api/user/ai/providers",
        json={
            "label": "Test Provider",
            "protocol": "bogus",
            "base_url": "https://api.example.com/v1",
            "api_key": RAW_KEY,
        },
    )
    assert response.status_code == 400
    assert "Protocol" in response.json()["detail"]


def test_create_provider_rejects_empty_fields(ai_client, dek):
    """Pydantic min_length guards reject empty fields with 422."""
    response = ai_client.post(
        "/api/user/ai/providers",
        json={
            "label": "",
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": RAW_KEY,
        },
    )
    assert response.status_code == 422


def test_list_providers_hides_blob(ai_client, dek, db_connection, test_user):
    _create_provider(ai_client)

    response = ai_client.get("/api/user/ai/providers")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert len(providers) == 1
    provider = providers[0]
    assert provider["api_key_masked"] == "•••"
    assert provider["model_count"] == 0
    assert RAW_KEY not in response.text
    assert "api_key_encrypted" not in response.text


def test_update_provider_empty_key_keeps_old_key(
    ai_client, dek, db_connection, test_user
):
    """api_key="" keeps the stored (still decryptable) key."""
    provider_id = _create_provider(ai_client)["provider"]["id"]

    response = ai_client.put(
        f"/api/user/ai/providers/{provider_id}", json={"api_key": ""}
    )
    assert response.status_code == 200
    assert response.json()["provider"]["api_key_masked"] == MASKED_KEY

    row = AiProviderRepository(db_connection).get_by_id(
        provider_id, test_user["id"]
    )
    plain = EncryptionService.decrypt_bytes(
        row["api_key_encrypted"], dek
    ).decode("utf-8")
    assert plain == RAW_KEY


def test_update_provider_replaces_key_and_label(
    ai_client, dek, db_connection, test_user
):
    new_key = "sk-brand-new-key-4321"
    provider_id = _create_provider(ai_client)["provider"]["id"]

    response = ai_client.put(
        f"/api/user/ai/providers/{provider_id}",
        json={"label": "Renamed", "api_key": new_key},
    )
    assert response.status_code == 200
    provider = response.json()["provider"]
    assert provider["label"] == "Renamed"
    assert provider["api_key_masked"] == "sk-…4321"
    assert new_key not in response.text

    row = AiProviderRepository(db_connection).get_by_id(
        provider_id, test_user["id"]
    )
    plain = EncryptionService.decrypt_bytes(
        row["api_key_encrypted"], dek
    ).decode("utf-8")
    assert plain == new_key


def test_delete_provider_clears_models_and_settings(
    ai_client, dek, db_connection, test_user
):
    provider_id = _create_provider(ai_client)["provider"]["id"]

    model_repo = AiModelRepository(db_connection)
    model_repo.replace_for_provider(
        provider_id,
        [{"model_id": "m-1", "display_name": "Model 1"}],
    )
    settings_repo = AiChatSettingsRepository(db_connection)
    settings_repo.ensure(test_user["id"])
    settings_repo.update(
        test_user["id"],
        {"active_provider_id": provider_id, "active_model_id": "m-1"},
    )

    response = ai_client.delete(f"/api/user/ai/providers/{provider_id}")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    assert model_repo.list_for_provider(provider_id) == []
    settings = settings_repo.ensure(test_user["id"])
    assert settings["active_provider_id"] is None
    assert settings["active_model_id"] is None
    assert not AiProviderRepository(db_connection).get_by_id(
        provider_id, test_user["id"]
    )


def test_delete_missing_provider_returns_404(ai_client, dek):
    response = ai_client.delete("/api/user/ai/providers/999999")
    assert response.status_code == 404


# ============================================================================
# Key roundtrip / active endpoint
# ============================================================================

def test_key_roundtrip_via_get_active_endpoint(
    ai_client, dek, db_connection, test_user
):
    """create -> select -> get_active_endpoint returns the plaintext key."""
    provider = _create_provider(ai_client)["provider"]
    provider_id = provider["id"]

    model_id = "gpt-test"
    AiModelRepository(db_connection).replace_for_provider(
        provider_id, [{"model_id": model_id, "display_name": "GPT Test"}]
    )

    response = ai_client.put(
        "/api/user/ai/settings",
        json={"active_provider_id": provider_id, "active_model_id": model_id},
    )
    assert response.status_code == 200

    service = _make_service(db_connection)
    endpoint = service.get_active_endpoint(test_user["id"], dek)
    assert endpoint is not None
    assert endpoint["api_key"] == RAW_KEY
    assert endpoint["base_url"] == "https://api.example.com/v1"
    assert endpoint["model_id"] == model_id
    assert endpoint["protocol"] == "openai_compatible"
    assert endpoint["temperature"] == 0.7


def test_get_active_endpoint_none_when_unset(ai_client, dek, db_connection, test_user):
    service = _make_service(db_connection)
    assert service.get_active_endpoint(test_user["id"], dek) is None


# ============================================================================
# Settings validation
# ============================================================================

def test_get_settings_defaults(ai_client, dek):
    response = ai_client.get("/api/user/ai/settings")
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["active_provider_id"] is None
    assert settings["active_model_id"] is None
    assert settings["temperature"] == 0.7
    assert settings["provider_label"] is None
    assert settings["model_display_name"] is None


def test_update_settings_unknown_provider_rejected(ai_client, dek):
    response = ai_client.put(
        "/api/user/ai/settings",
        json={"active_provider_id": 999999, "active_model_id": "m"},
    )
    assert response.status_code == 400


def test_update_settings_mismatched_model_rejected(ai_client, dek, db_connection):
    provider_id = _create_provider(ai_client)["provider"]["id"]
    AiModelRepository(db_connection).replace_for_provider(
        provider_id, [{"model_id": "m-1", "display_name": "Model 1"}]
    )

    response = ai_client.put(
        "/api/user/ai/settings",
        json={"active_provider_id": provider_id, "active_model_id": "not-here"},
    )
    assert response.status_code == 400


def test_update_settings_model_without_provider_rejected(ai_client, dek):
    response = ai_client.put(
        "/api/user/ai/settings",
        json={"active_provider_id": None, "active_model_id": "m-1"},
    )
    assert response.status_code == 400


def test_update_settings_valid_pair_persists(ai_client, dek, db_connection):
    provider_id = _create_provider(ai_client)["provider"]["id"]
    AiModelRepository(db_connection).replace_for_provider(
        provider_id,
        [{"model_id": "m-1", "display_name": "Model 1", "supports_vision": 1}],
    )

    response = ai_client.put(
        "/api/user/ai/settings",
        json={"active_provider_id": provider_id, "active_model_id": "m-1"},
    )
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["active_provider_id"] == provider_id
    assert settings["active_model_id"] == "m-1"
    assert settings["provider_label"] == "Test Provider"
    assert settings["protocol"] == "openai_compatible"
    assert settings["model_display_name"] == "Model 1"
    assert settings["supports_vision"] is True
    assert settings["supports_tools"] is False

    # GET returns the same enriched view.
    fetched = ai_client.get("/api/user/ai/settings").json()["settings"]
    assert fetched["active_provider_id"] == provider_id
    assert fetched["provider_label"] == "Test Provider"


# ============================================================================
# fetch-models with a stubbed LLM client
# ============================================================================

def test_fetch_models_merges_and_persists(
    ai_client, dek, db_connection, test_user, monkeypatch
):
    provider_id = _create_provider(ai_client)["provider"]["id"]

    model_repo = AiModelRepository(db_connection)
    model_repo.replace_for_provider(
        provider_id,
        [
            {
                "model_id": "model-a",
                "display_name": "Old A",
                "is_pinned": 1,
                "context_tokens": 123,
                "max_output_tokens": 456,
                "limits_source": "manual",
            }
        ],
    )

    fetched = [
        _FetchedModel(
            model_id="model-a",
            display_name="Model A",
            supports_tools=True,
            context_tokens=None,
            max_output_tokens=None,
            limits_source=None,
        ),
        _FetchedModel(
            model_id="model-b",
            display_name="Model B",
            supports_vision=True,
            context_tokens=8192,
            max_output_tokens=4096,
            limits_source="api",
        ),
    ]
    stub = _StubClient(models=fetched)
    _patch_llm(monkeypatch, stub, _StubError)

    response = ai_client.post(
        f"/api/user/ai/providers/{provider_id}/fetch-models"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fetched"] == 2
    assert {m["model_id"] for m in data["models"]} == {"model-a", "model-b"}

    # The stub received the decrypted key and the stored base URL.
    assert stub.calls == [("https://api.example.com/v1", RAW_KEY)]

    rows = {r["model_id"]: r for r in model_repo.list_for_provider(provider_id)}
    # Known model: pinned flag and manually maintained limits survive.
    assert rows["model-a"]["is_pinned"] == 1
    assert rows["model-a"]["context_tokens"] == 123
    assert rows["model-a"]["max_output_tokens"] == 456
    assert rows["model-a"]["limits_source"] == "manual"
    # Fresh metadata (display name / capability flags) wins.
    assert rows["model-a"]["display_name"] == "Model A"
    assert rows["model-a"]["supports_tools"] == 1
    # New model: defaults apply.
    assert rows["model-b"]["is_pinned"] == 0
    assert rows["model-b"]["context_tokens"] == 8192
    assert rows["model-b"]["limits_source"] == "api"
    assert rows["model-b"]["supports_vision"] == 1


def test_fetch_models_missing_provider_404(ai_client, dek, monkeypatch):
    stub = _StubClient(models=[])
    _patch_llm(monkeypatch, stub, _StubError)

    response = ai_client.post("/api/user/ai/providers/999999/fetch-models")
    assert response.status_code == 404


def test_fetch_models_llm_error_maps_to_502(ai_client, dek, monkeypatch):
    provider_id = _create_provider(ai_client)["provider"]["id"]

    if llm_pkg is not None:
        error_cls = llm_pkg.LLMError
    else:
        error_cls = _StubError
    stub = _StubClient(error=error_cls("provider exploded"))
    _patch_llm(monkeypatch, stub, error_cls)

    response = ai_client.post(
        f"/api/user/ai/providers/{provider_id}/fetch-models"
    )
    assert response.status_code == 502
    assert "provider exploded" in response.json()["detail"]


# ============================================================================
# Service-level helpers
# ============================================================================

def test_mask_key():
    assert AiProviderService.mask_key("abcd12345xyz6789") == "abc…6789"
    assert AiProviderService.mask_key("short") == "•••"
    assert AiProviderService.mask_key("123456789") == "•••"  # len == 9
    assert AiProviderService.mask_key("1234567890") == "123…7890"
