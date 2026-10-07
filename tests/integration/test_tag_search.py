"""
Tag search endpoint regression tests.

These endpoints power the search-box suggestions and the tag-input
suggestions; a param/placeholder mismatch after the display_name removal
broke all of them at once (500 on every keystroke), so each WHERE variant
is covered here:
- GET /api/tags/search  (repository search(): WHERE + relevance CASE)
- GET /api/tags?q=      (repository list_tags(): LIKE condition + LIMIT/OFFSET)
- GET /api/tags         (unpaginated use by /api/ai/tags, limit=None)
"""
import pytest
from fastapi.testclient import TestClient

from app.infrastructure.repositories import TagsRepository


@pytest.fixture(scope="function")
def sample_tags(db_connection) -> list[int]:
    db_connection.execute(
        "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
        "VALUES (1, 'general', 'General', '#6b7280', 1)"
    )
    db_connection.commit()
    repo = TagsRepository(db_connection)
    ids = [repo.create(name, 1) for name in ("fox", "fox_terrier", "wolf", "night_fox")]
    db_connection.commit()
    return ids


def _redirected_to_login(resp) -> bool:
    """The TestClient follows redirects: an unauthenticated GET lands on
    the login page instead of returning a 4xx."""
    return bool(resp.history) and resp.url.path.endswith("/login")


class TestTagSearchEndpoint:

    def test_search_requires_auth(self, client: TestClient, sample_tags):
        resp = client.get("/api/tags/search", params={"q": "fox"})
        assert _redirected_to_login(resp)

    def test_search_returns_matches(self, authenticated_client: TestClient, sample_tags):
        resp = authenticated_client.get("/api/tags/search", params={"q": "fox"})
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tags"]]
        assert set(names) == {"fox", "fox_terrier", "night_fox"}
        # relevance: exact match first
        assert names[0] == "fox"

    def test_search_substring_match(self, authenticated_client: TestClient, sample_tags):
        resp = authenticated_client.get("/api/tags/search", params={"q": "terr"})
        assert resp.status_code == 200
        assert [t["name"] for t in resp.json()["tags"]] == ["fox_terrier"]

    def test_search_no_match(self, authenticated_client: TestClient, sample_tags):
        resp = authenticated_client.get("/api/tags/search", params={"q": "dragon"})
        assert resp.status_code == 200
        assert resp.json()["tags"] == []


class TestTagListEndpoint:

    def test_list_requires_auth(self, client: TestClient, sample_tags):
        resp = client.get("/api/tags", params={"q": "fox"})
        assert _redirected_to_login(resp)

    def test_list_filters_by_query(self, authenticated_client: TestClient, sample_tags):
        resp = authenticated_client.get("/api/tags", params={"q": "fox"})
        assert resp.status_code == 200
        data = resp.json()
        assert {t["name"] for t in data["items"]} == {"fox", "fox_terrier", "night_fox"}
        assert data["total"] == 3  # count_tags() must agree with list_tags()

    def test_list_paginates_with_query(self, authenticated_client: TestClient, sample_tags):
        resp = authenticated_client.get(
            "/api/tags", params={"q": "fox", "limit": 1, "offset": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total"] == 3
