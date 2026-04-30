"""AI tagging service - job queue business logic."""
from typing import List, Dict, Optional

from fastapi import HTTPException


class AITaggingService:
    """Service for AI tagging job queue operations."""

    def __init__(
        self,
        job_repo,
        tag_service,
        item_repo,
        item_media_repo=None
    ):
        self.jobs = job_repo
        self.tags = tag_service
        self.items = item_repo
        self.item_media = item_media_repo

    def create_jobs(self, item_ids: List[str]) -> List[Dict]:
        """Create pending jobs for given item IDs.

        Returns:
            List of created job dicts
        """
        job_ids = self.jobs.create_jobs(item_ids)
        result = []
        for job_id in job_ids:
            job = self.jobs.get_job_by_id(job_id)
            if job:
                result.append(job)
        return result

    def get_pending_jobs(self, limit: int = 10) -> List[Dict]:
        """Get pending jobs."""
        return self.jobs.get_pending(limit)

    def claim_job(self, job_id: int) -> Optional[Dict]:
        """Claim a job and return enriched data with item metadata.

        Returns:
            Dict with job and item metadata, or None if claim failed
        """
        claimed = self.jobs.claim_job(job_id)
        if not claimed:
            return None

        job = self.jobs.get_job_by_id(job_id)
        if not job:
            return None

        item_id = job["item_id"]
        item = self.items.get_by_id(item_id) if self.items else None
        media = None
        if item and self.item_media:
            media = self.item_media.get_by_item_id(item_id)

        # Get existing tags for this item
        existing_tags = []
        if self.tags:
            try:
                tag_data = self.tags.get_item_tags(item_id)
                existing_tags = [t["id"] for t in tag_data.get("all_tags", [])]
            except HTTPException:
                pass

        enriched = {
            "job": {
                "id": job["id"],
                "item_id": item_id,
                "status": job["status"],
                "created_at": job.get("created_at"),
                "retry_count": job.get("retry_count", 0),
            },
            "item": {
                "id": item_id,
                "title": item.get("title") if item else None,
                "description": item.get("description") if item else None,
                "file_url": f"/files/{item_id}",
                "media_type": media.get("media_type") if media else None,
                "content_type": media.get("content_type") if media else None,
            },
            "existing_tags": existing_tags,
        }
        return enriched

    def submit_results(self, job_id: int, tag_ids: List[int]) -> bool:
        """Submit tag results for a job and resolve implications.

        Merges new tags with existing explicit tags to preserve user-added tags.

        Args:
            job_id: Job ID
            tag_ids: List of tag IDs identified by AI agent

        Returns:
            True if successful
        """
        job = self.jobs.get_job_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] != "processing":
            raise HTTPException(status_code=400, detail="Job is not in processing state")

        item_id = job["item_id"]

        # Merge with existing explicit tags to avoid removing user-added tags
        existing_explicit = []
        if self.tags:
            try:
                tag_data = self.tags.get_item_tags(item_id)
                existing_explicit = [t["id"] for t in tag_data.get("explicit_tags", [])]
            except HTTPException:
                pass

        merged_tag_ids = list(set(existing_explicit) | set(tag_ids))

        # Apply tags via TagService (resolves implications automatically)
        if self.tags:
            self.tags.set_item_tags(item_id, merged_tag_ids)

        # Mark job as completed
        return self.jobs.complete_job(job_id, tag_ids)

    def fail_job(self, job_id: int, error: str) -> bool:
        """Mark job as failed."""
        return self.jobs.fail_job(job_id, error)

    def get_job_progress(self, job_ids: List[int]) -> Dict:
        """Get progress statistics for given job IDs."""
        return self.jobs.get_stats(job_ids)
