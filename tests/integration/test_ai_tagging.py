"""
AI Tagging Job Queue integration tests.

Verifies:
- Job creation via session auth
- Pending jobs listing via API key
- Atomic job claim
- Tag result submission with implication resolution
- Job failure handling
- Progress polling
- File access for agents (non-encrypted only)
"""
import hashlib

import pytest
from fastapi.testclient import TestClient


class TestAITaggingJobs:
    """Test AI tagging job queue lifecycle."""

    @pytest.fixture(scope="function")
    def api_key(self, db_connection, test_user) -> str:
        """Create an API key for agent authentication bound to test_user."""
        raw_key = "test-api-key-12345"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        db_connection.execute(
            "INSERT INTO ai_api_keys (name, key_hash, is_active, user_id) VALUES (?, ?, 1, ?)",
            ("Test Agent", key_hash, test_user["id"])
        )
        db_connection.commit()
        return raw_key

    @pytest.fixture(scope="function")
    def test_tags(self, db_connection) -> list:
        """Create test tags for result submission."""
        from app.infrastructure.repositories import TagsRepository
        repo = TagsRepository(db_connection)
        # Ensure category exists
        cursor = db_connection.execute(
            "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) VALUES (1, 'general', 'General', '#6b7280', 1)"
        )
        db_connection.commit()
        tag_ids = []
        for name in ("fox", "wolf", "animal"):
            tid = repo.create(name, name.title(), 1)
            tag_ids.append(tid)
        return tag_ids

    def test_create_jobs_requires_auth(self, client: TestClient):
        """Job creation requires session authentication."""
        resp = client.post(
            "/api/ai/jobs",
            json={"item_ids": ["nonexistent-id"]}
        )
        assert resp.status_code == 401

    def test_create_jobs(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str
    ):
        """Authenticated user can create tagging jobs."""
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["item_id"] == uploaded_photo["id"]
        assert data["jobs"][0]["status"] == "pending"

    def test_get_pending_requires_api_key(self, client: TestClient):
        """Pending jobs endpoint requires API key."""
        resp = client.get("/api/ai/jobs/pending", follow_redirects=False)
        # Without API key or session, middleware returns 302 (redirect to login)
        # or 401 depending on method and endpoint
        assert resp.status_code in (302, 401)

    def test_get_pending_jobs(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str
    ):
        """Agent can list pending jobs with valid API key."""
        # Create a job first
        authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )

        resp = authenticated_client.get(
            "/api/ai/jobs/pending",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) >= 1
        assert data["jobs"][0]["status"] == "pending"

    def test_claim_job(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str
    ):
        """Agent can atomically claim a pending job."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        job_id = resp.json()["jobs"][0]["id"]

        # Claim
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job"]["id"] == job_id
        assert data["job"]["item_id"] == uploaded_photo["id"]
        assert "item" in data
        assert "existing_tags" in data

        # Second claim should fail (already processing)
        resp2 = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )
        assert resp2.status_code == 409

    def test_submit_results(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str,
        test_tags: list
    ):
        """Agent can submit tag results and resolve implications."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        job_id = resp.json()["jobs"][0]["id"]

        # Claim
        authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )

        # Submit results
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/results",
            json={"tag_ids": test_tags},
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify tags were applied
        resp = authenticated_client.get(f"/api/items/{uploaded_photo['id']}/tags")
        assert resp.status_code == 200
        data = resp.json()
        applied_tag_ids = [t["id"] for t in data.get("explicit_tags", [])]
        for tid in test_tags:
            assert tid in applied_tag_ids

    def test_fail_job(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str
    ):
        """Agent can mark a job as failed."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        job_id = resp.json()["jobs"][0]["id"]

        # Claim
        authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )

        # Fail
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/fail",
            json={"error": "VLM timeout"},
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_progress_polling(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str,
        test_tags: list
    ):
        """User can poll job progress."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        job_id = resp.json()["jobs"][0]["id"]

        # Check progress before completion
        resp = authenticated_client.get(
            f"/api/ai/jobs/progress?job_ids={job_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["pending"] == 1

        # Claim and complete
        authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )
        authenticated_client.post(
            f"/api/ai/jobs/{job_id}/results",
            json={"tag_ids": test_tags[:1]},
            headers={"X-API-Key": api_key}
        )

        # Check progress after completion
        resp = authenticated_client.get(
            f"/api/ai/jobs/progress?job_ids={job_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] == 1

    def test_file_access_requires_api_key(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict
    ):
        """File access endpoint requires API key."""
        resp = authenticated_client.get(
            f"/api/ai/items/{uploaded_photo['id']}/file"
        )
        assert resp.status_code == 401

    def test_file_access_non_encrypted(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        api_key: str
    ):
        """Agent can download non-encrypted item files."""
        resp = authenticated_client.get(
            f"/api/ai/items/{uploaded_photo['id']}/file",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_file_access_rejects_encrypted(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        test_folder: str,
        api_key: str,
        db_connection
    ):
        """Agent cannot download encrypted items."""
        from app.infrastructure.repositories import ItemRepository
        item_repo = ItemRepository(db_connection)
        item_id = item_repo.create(
            item_type="media",
            folder_id=test_folder,
            user_id=test_user["id"],
            is_encrypted=True
        )

        # Agent should be rejected
        resp = authenticated_client.get(
            f"/api/ai/items/{item_id}/file",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 403

    def test_get_ai_tags_requires_api_key(self, authenticated_client: TestClient):
        """Tag list endpoint requires API key."""
        resp = authenticated_client.get("/api/ai/tags")
        assert resp.status_code == 401

    def test_get_ai_tags(self, authenticated_client: TestClient, api_key: str, test_tags: list):
        """Agent can fetch full tag dictionary."""
        resp = authenticated_client.get(
            "/api/ai/tags",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tags" in data
        assert data["total"] >= 3
        # Check structure
        tag = data["tags"][0]
        assert "id" in tag
        assert "name" in tag
        assert "display_name" in tag
        assert "category" in tag

    def test_submit_results_by_tag_names(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        uploaded_photo: dict,
        api_key: str,
        test_tags: list
    ):
        """Agent can submit results using tag names instead of IDs."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]}
        )
        assert resp.status_code == 200
        job_id = resp.json()["jobs"][0]["id"]

        # Claim
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200

        # Submit by names
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/results",
            headers={"X-API-Key": api_key},
            json={"tag_names": ["fox", "wolf"]}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_submit_results_unknown_tag_names(
        self,
        authenticated_client: TestClient,
        test_user: dict,
        uploaded_photo: dict,
        api_key: str,
        test_tags: list
    ):
        """Submitting unknown tag names returns specific error with unknown tags list."""
        # Create job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]}
        )
        assert resp.status_code == 200
        job_id = resp.json()["jobs"][0]["id"]

        # Claim
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/claim",
            headers={"X-API-Key": api_key}
        )
        assert resp.status_code == 200

        # Submit unknown names
        resp = authenticated_client.post(
            f"/api/ai/jobs/{job_id}/results",
            headers={"X-API-Key": api_key},
            json={"tag_names": ["dragon", "unicorn", "fox"]}
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "unknown_tags" in data["detail"]
        assert sorted(data["detail"]["unknown_tags"]) == ["dragon", "unicorn"]
        assert "fox" not in data["detail"]["unknown_tags"]  # fox exists
