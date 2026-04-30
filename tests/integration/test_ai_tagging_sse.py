"""
AI Tagging SSE and active jobs integration tests.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient


class TestAITaggingSSE:
    """Test SSE progress streaming and active jobs recovery."""

    @pytest.fixture(scope="function")
    def api_key(self, db_connection) -> str:
        """Create an API key for agent authentication."""
        raw_key = "test-api-key-sse"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        db_connection.execute(
            "INSERT INTO ai_api_keys (name, key_hash, is_active) VALUES (?, ?, 1)",
            ("Test Agent", key_hash)
        )
        db_connection.commit()
        return raw_key

    def test_sse_stream_returns_events(self, authenticated_client: TestClient):
        """SSE endpoint returns event-stream and closes when no active jobs."""
        resp = authenticated_client.get("/api/ai/jobs/events")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"

    def test_active_jobs_returns_user_jobs(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str
    ):
        """Active jobs endpoint returns pending/processing jobs for current user."""
        # Initially empty
        resp = authenticated_client.get("/api/ai/jobs/active")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

        # Create a job
        resp = authenticated_client.post(
            "/api/ai/jobs",
            json={"item_ids": [uploaded_photo["id"]]},
            headers={"X-CSRF-Token": csrf_token}
        )
        assert resp.status_code == 200

        # Should now have active job
        resp = authenticated_client.get("/api/ai/jobs/active")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["status"] == "pending"

    def test_reap_stale_jobs_requires_admin(
        self,
        authenticated_client: TestClient
    ):
        """Reaper endpoint requires admin access."""
        resp = authenticated_client.post("/api/ai/jobs/reap")
        assert resp.status_code == 403

    def test_reap_stale_jobs(
        self,
        client: TestClient,
        db_connection,
        test_user: dict,
        uploaded_photo: dict
    ):
        """Admin can reap stale processing jobs."""
        from app.infrastructure.repositories import AIJobRepository

        # Make user admin
        db_connection.execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?",
            (test_user["id"],)
        )
        db_connection.commit()

        # Login
        client.post("/login", data={
            "username": test_user["username"],
            "password": test_user["password"]
        }, follow_redirects=False)

        # Create and manually claim a job with expired deadline
        repo = AIJobRepository(db_connection)
        job_ids = repo.create_jobs([uploaded_photo["id"]], test_user["id"])
        db_connection.execute(
            """UPDATE ai_tagging_jobs
               SET status = 'processing',
                   processing_deadline = datetime('now', '-1 hour')
               WHERE id = ?""",
            (job_ids[0],)
        )
        db_connection.commit()

        # Reap
        resp = client.post("/api/ai/jobs/reap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["retried"] >= 1

        # Job should be back to pending
        job = repo.get_job_by_id(job_ids[0])
        assert job["status"] == "pending"
        assert job["retry_count"] == 1

    def test_claim_sets_processing_deadline(
        self,
        authenticated_client: TestClient,
        uploaded_photo: dict,
        csrf_token: str,
        api_key: str,
        db_connection
    ):
        """Claiming a job sets processing_deadline."""
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

        # Verify deadline is set
        cursor = db_connection.execute(
            "SELECT processing_deadline FROM ai_tagging_jobs WHERE id = ?",
            (job_id,)
        )
        row = cursor.fetchone()
        assert row["processing_deadline"] is not None
