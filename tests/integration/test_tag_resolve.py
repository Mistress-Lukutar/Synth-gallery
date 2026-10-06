"""
POST /api/tags/resolve integration tests.

Verifies:
- Lookup of existing tags with usage counts (input order preserved)
- Unknown names stay un-created without create_missing
- Creation of unknown tags in the "general" category (admin only)
- 403 for non-admin create_missing
- Invalid names flagged as valid=False and never created
- Duplicate input names are safe
- Missing "general" category is a hard error for creation
"""
import pytest
from fastapi.testclient import TestClient

from app.config import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from tests.conftest import login_as


def _post(client: TestClient, payload: dict) -> TestClient.post:
    """POST to /api/tags/resolve with the client's CSRF token attached."""
    return client.post(
        "/api/tags/resolve",
        json=payload,
        headers={CSRF_HEADER_NAME: client.cookies.get(CSRF_COOKIE_NAME, "")},
    )


def _count(db_connection, sql: str, params: tuple = ()) -> int:
    return db_connection.execute(sql, params).fetchone()["cnt"]


class TestTagResolve:

    @pytest.fixture(scope="function")
    def general_category(self, db_connection) -> None:
        """Fresh databases have no tag categories; create 'general'."""
        db_connection.execute(
            "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
            "VALUES (1, 'general', 'General', '#6b7280', 1)"
        )
        db_connection.commit()

    @pytest.fixture(scope="function")
    def fox_tag(self, db_connection, general_category) -> int:
        from app.infrastructure.repositories import TagsRepository
        return TagsRepository(db_connection).create("fox", "Fox", 1)

    def test_requires_auth(self, client: TestClient):
        resp = client.post("/api/tags/resolve", json={"names": ["fox"]})
        assert resp.status_code in (401, 403)  # rejected before any work happens

    def test_resolve_existing_and_unknown(
            self, authenticated_client: TestClient, fox_tag: int):
        resp = _post(authenticated_client, {"names": ["Fox", "nope"]})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert [r["input"] for r in results] == ["Fox", "nope"]

        fox = results[0]
        assert fox["valid"] is True
        assert fox["exists"] is True
        assert fox["created"] is False
        assert fox["tag"]["id"] == fox_tag
        assert fox["tag"]["name"] == "fox"  # normalized to the DB name
        assert fox["tag"]["usage_count"] == 0

        nope = results[1]
        assert nope["valid"] is True
        assert nope["exists"] is False
        assert nope["created"] is False
        assert nope["tag"] is None

    def test_invalid_names_are_flagged_not_created(
            self, client: TestClient, db_connection, test_user, general_category):
        db_connection.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?", (test_user["id"],))
        db_connection.commit()
        with login_as(client, test_user["username"], test_user["password"]):
            # Even an admin with create_missing must not create invalid names
            resp = _post(client, {"names": ["привет", "a;b"], "create_missing": True})
        assert resp.status_code == 200
        for entry in resp.json()["results"]:
            assert entry["valid"] is False
            assert entry["exists"] is False
            assert entry["tag"] is None
        assert _count(db_connection, "SELECT COUNT(*) as cnt FROM tags") == 0

    def test_unknown_not_created_without_flag(
            self, authenticated_client: TestClient, db_connection, general_category):
        resp = _post(authenticated_client,
                     {"names": ["brand_new"], "create_missing": False})
        assert resp.status_code == 200
        entry = resp.json()["results"][0]
        assert entry["exists"] is False
        assert _count(db_connection,
                      "SELECT COUNT(*) as cnt FROM tags WHERE name = 'brand_new'") == 0

    def test_create_missing_requires_admin(
            self, authenticated_client: TestClient, db_connection, general_category):
        resp = _post(authenticated_client,
                     {"names": ["brand_new"], "create_missing": True})
        assert resp.status_code == 403
        assert _count(db_connection,
                      "SELECT COUNT(*) as cnt FROM tags WHERE name = 'brand_new'") == 0

    def test_create_missing_as_admin_creates_in_general(
            self, client: TestClient, db_connection, test_user, general_category):
        db_connection.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?", (test_user["id"],))
        db_connection.commit()
        with login_as(client, test_user["username"], test_user["password"]):
            resp = _post(client, {"names": ["New Tag"], "create_missing": True})
        assert resp.status_code == 200
        entry = resp.json()["results"][0]
        assert entry["valid"] is True
        assert entry["exists"] is True
        assert entry["created"] is True
        assert entry["tag"]["name"] == "new_tag"  # spaces normalized to underscores
        assert entry["tag"]["usage_count"] == 0
        assert entry["tag"]["category_name"] == "General"

    def test_create_missing_without_general_category_fails(
            self, client: TestClient, db_connection, test_user):
        db_connection.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?", (test_user["id"],))
        db_connection.commit()
        with login_as(client, test_user["username"], test_user["password"]):
            resp = _post(client, {"names": ["anything"], "create_missing": True})
        assert resp.status_code == 400

    def test_duplicate_names_are_safe(
            self, client: TestClient, db_connection, test_user, general_category, fox_tag):
        db_connection.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?", (test_user["id"],))
        db_connection.commit()
        with login_as(client, test_user["username"], test_user["password"]):
            resp = _post(client, {
                "names": ["FOX", "fox", "Fresh", "fresh", "french fries"],
                "create_missing": True,
            })
        assert resp.status_code == 200
        results = resp.json()["results"]

        fox_entries = [r for r in results if r["name"] == "fox"]
        assert len(fox_entries) == 2
        assert all(r["tag"]["id"] == fox_tag for r in fox_entries)
        assert all(r["created"] is False for r in fox_entries)

        fresh_entries = [r for r in results if r["name"] == "fresh"]
        assert len(fresh_entries) == 2
        created_ids = {r["tag"]["id"] for r in fresh_entries}
        assert len(created_ids) == 1  # created once, resolved for both
        assert all(r["created"] is True for r in fresh_entries)

        fries = next(r for r in results if r["name"] == "french_fries")
        assert fries["created"] is True

        assert _count(db_connection,
                      "SELECT COUNT(*) as cnt FROM tags WHERE name IN ('fresh', 'french_fries')") == 2

    def test_empty_names_rejected(self, authenticated_client: TestClient):
        resp = _post(authenticated_client, {"names": []})
        assert resp.status_code == 422
