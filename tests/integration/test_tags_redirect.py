"""Regression tests for the legacy /tags page redirect.

The standalone /tags route in app/routes/admin_tags.py now redirects to the
in-gallery SPA tags view (/?view=tags).  Every path through that endpoint
requires authentication (require_user).
"""
import pytest
from fastapi.testclient import TestClient


def _redirected_to_login(resp) -> bool:
    """The TestClient follows redirects: an unauthenticated GET lands on
    the login page instead of returning a 4xx."""
    return bool(resp.history) and resp.url.path.endswith("/login")


class TestTagsPageRedirect:

    def test_redirect_to_tags_view(self, authenticated_client: TestClient):
        """Authenticated user GET /tags -> 302 to ?view=tags."""
        resp = authenticated_client.get("/tags", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.endswith("/?view=tags")

    def test_redirect_preserves_tag_param(self, authenticated_client: TestClient):
        """Authenticated user GET /tags?tag=silver_fox -> Location contains tag."""
        resp = authenticated_client.get(
            "/tags", params={"tag": "silver_fox"}, follow_redirects=False
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.endswith("/?view=tags&tag=silver_fox")

    def test_redirect_preserves_encoded_tag_param(self, authenticated_client: TestClient):
        """Percent-encoded tag value is preserved through the redirect."""
        resp = authenticated_client.get(
            "/tags", params={"tag": "two words"}, follow_redirects=False
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.endswith("/?view=tags&tag=two%20words")

    def test_unauthenticated_get_not_content(self, client: TestClient):
        """Unauthenticated GET /tags redirects to login (not 200/302-to-content)."""
        resp = client.get("/tags")
        assert _redirected_to_login(resp)
