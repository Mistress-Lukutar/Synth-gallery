"""AI tagging job repository - job queue operations."""
import json
from typing import Optional, List, Dict

from .base import Repository


class AIJobRepository(Repository):
    """Repository for AI tagging job queue operations."""

    def create_jobs(self, item_ids: List[str]) -> List[int]:
        """Create pending jobs for given item IDs.

        Returns:
            List of created job IDs
        """
        job_ids = []
        for item_id in item_ids:
            cursor = self._execute(
                """INSERT INTO ai_tagging_jobs (item_id, status, retry_count)
                   VALUES (?, 'pending', 0)""",
                (item_id,)
            )
            job_ids.append(cursor.lastrowid)
        self._commit()
        return job_ids

    def get_pending(self, limit: int = 10) -> List[Dict]:
        """Get pending jobs ordered by creation time."""
        cursor = self._execute(
            """SELECT id, item_id, status, created_at, retry_count
               FROM ai_tagging_jobs
               WHERE status = 'pending'
               ORDER BY created_at ASC
               LIMIT ?""",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def claim_job(self, job_id: int) -> bool:
        """Atomically claim a pending job (pending -> processing).

        Returns:
            True if job was claimed, False if not in pending state
        """
        self._execute(
            """UPDATE ai_tagging_jobs
               SET status = 'processing', started_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'pending'""",
            (job_id,)
        )
        self._commit()
        return self._conn.total_changes > 0

    def complete_job(self, job_id: int, tag_ids: List[int]) -> bool:
        """Mark job as completed with result tags."""
        result_tags_json = json.dumps(tag_ids) if tag_ids else None
        self._execute(
            """UPDATE ai_tagging_jobs
               SET status = 'completed',
                   completed_at = CURRENT_TIMESTAMP,
                   result_tags = ?
               WHERE id = ? AND status = 'processing'""",
            (result_tags_json, job_id)
        )
        self._commit()
        return self._conn.total_changes > 0

    def fail_job(self, job_id: int, error: str) -> bool:
        """Mark job as failed with error message and increment retry count."""
        self._execute(
            """UPDATE ai_tagging_jobs
               SET status = 'failed',
                   completed_at = CURRENT_TIMESTAMP,
                   error_message = ?,
                   retry_count = retry_count + 1
               WHERE id = ?""",
            (error, job_id)
        )
        self._commit()
        return self._conn.total_changes > 0

    def get_job_by_id(self, job_id: int) -> Optional[Dict]:
        """Get job by ID."""
        cursor = self._execute(
            """SELECT id, item_id, status, created_at, started_at,
                      completed_at, result_tags, error_message, retry_count
               FROM ai_tagging_jobs
               WHERE id = ?""",
            (job_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        job = dict(row)
        if job.get("result_tags"):
            try:
                job["result_tags"] = json.loads(job["result_tags"])
            except json.JSONDecodeError:
                job["result_tags"] = None
        return job

    def get_jobs_for_item(self, item_id: str) -> List[Dict]:
        """Get all jobs for a specific item."""
        cursor = self._execute(
            """SELECT id, item_id, status, created_at, started_at,
                      completed_at, result_tags, error_message, retry_count
               FROM ai_tagging_jobs
               WHERE item_id = ?
               ORDER BY created_at DESC""",
            (item_id,)
        )
        jobs = []
        for row in cursor.fetchall():
            job = dict(row)
            if job.get("result_tags"):
                try:
                    job["result_tags"] = json.loads(job["result_tags"])
                except json.JSONDecodeError:
                    job["result_tags"] = None
            jobs.append(job)
        return jobs

    def get_stats(self, job_ids: List[int]) -> Dict:
        """Get status counts for given job IDs.

        Returns:
            Dict with total, completed, failed, pending, processing counts
        """
        if not job_ids:
            return {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "pending": 0,
                "processing": 0
            }
        placeholders = ','.join('?' * len(job_ids))
        cursor = self._execute(
            f"""SELECT status, COUNT(*) as cnt
                FROM ai_tagging_jobs
                WHERE id IN ({placeholders})
                GROUP BY status""",
            tuple(job_ids)
        )
        stats = {
            "total": len(job_ids),
            "completed": 0,
            "failed": 0,
            "pending": 0,
            "processing": 0
        }
        for row in cursor.fetchall():
            stats[row["status"]] = row["cnt"]
        return stats
