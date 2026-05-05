"""Tag suggestion service - related tags and contextual recommendations."""
import math
from typing import List, Dict, Optional

from ...infrastructure.repositories import (
    TagCooccurrenceRepository,
    TagsRepository,
    TagMutexRepository,
    TagFeedbackRepository,
)


class TagSuggestionService:
    """Service for tag recommendations based on co-occurrence, mutex, and feedback."""

    # Scoring weights (tunable)
    W_PMI = 1.0
    W_MUTEX = 2.0
    W_FEEDBACK = 0.5

    def __init__(self,
                 cooccurrence_repo: TagCooccurrenceRepository,
                 tags_repo: TagsRepository,
                 mutex_repo: Optional[TagMutexRepository] = None,
                 feedback_repo: Optional[TagFeedbackRepository] = None):
        self.cooccurrence = cooccurrence_repo
        self.tags = tags_repo
        self.mutex = mutex_repo
        self.feedback = feedback_repo

    # ========================================================================
    # Core scoring
    # ========================================================================

    def _score_candidates(self, candidates: List[Dict],
                          current_tag_ids: List[int]) -> List[Dict]:
        """Score a batch of candidates using PMI, mutex, and feedback."""
        if not candidates:
            return []

        total = self.cooccurrence._total_items()
        all_tag_ids = list(set(current_tag_ids + [c["id"] for c in candidates]))
        usage_counts = self.cooccurrence.get_usage_counts(all_tag_ids)

        # Preload joint counts for all (selected, candidate) pairs
        joint_counts = {}
        for tid in current_tag_ids:
            related_ids = [c["id"] for c in candidates]
            jc = self.cooccurrence.get_joint_counts(tid, related_ids)
            joint_counts[tid] = jc

        # Preload mutex info
        mutex_map = {}
        if self.mutex is not None and current_tag_ids:
            mutex_entries = self.mutex.get_mutex_for_tags(current_tag_ids)
            for e in mutex_entries:
                pair = tuple(sorted([e["tag_a_id"], e["tag_b_id"]]))
                mutex_map[pair] = abs(e["phi"])

        # Preload feedback stats
        feedback_stats = {}
        if self.feedback is not None:
            for c in candidates:
                accepts, rejects = self.feedback.get_stats_for_tags(c["id"], current_tag_ids)
                feedback_stats[c["id"]] = (accepts, rejects)

        scored = []
        for cand in candidates:
            cid = cand["id"]
            usage_c = usage_counts.get(cid, 0)

            # PMI score (average across current tags)
            pmi_sum = 0.0
            pmi_count = 0
            for tid in current_tag_ids:
                usage_t = usage_counts.get(tid, 0)
                joint = joint_counts.get(tid, {}).get(cid, 0)
                if total > 0 and usage_t > 0 and usage_c > 0 and joint > 0:
                    pmi = math.log2((joint * total) / (usage_t * usage_c))
                else:
                    pmi = 0.0
                pmi_sum += pmi
                pmi_count += 1
            pmi_score = (pmi_sum / pmi_count) if pmi_count > 0 else 0.0

            # Mutex penalty
            mutex_penalty = 0.0
            for tid in current_tag_ids:
                pair = tuple(sorted([tid, cid]))
                phi = mutex_map.get(pair, 0.0)
                if phi:
                    mutex_penalty += phi * self.W_MUTEX

            # Feedback boost
            feedback_boost = 0.0
            if cid in feedback_stats:
                accepts, rejects = feedback_stats[cid]
                if accepts > 0 or rejects > 0:
                    feedback_boost = math.log((accepts + 1) / (rejects + 1)) * self.W_FEEDBACK

            total_score = (self.W_PMI * pmi_score) - mutex_penalty + feedback_boost
            scored.append({**cand, "suggestion_score": round(total_score, 3)})

        scored.sort(key=lambda x: x["suggestion_score"], reverse=True)
        return scored

    def score_candidate(self, candidate_id: int,
                        current_tag_ids: List[int]) -> float:
        """Score a single candidate (slower, for one-off use)."""
        candidates = [{"id": candidate_id}]
        result = self._score_candidates(candidates, current_tag_ids)
        return result[0]["suggestion_score"] if result else 0.0

    # ========================================================================
    # Public API
    # ========================================================================

    def get_related_for_tag(self, tag_id: int, limit: int = 8,
                            exclude_ids: Optional[List[int]] = None) -> List[Dict]:
        """Get tags most frequently co-occurring with a single tag."""
        # Use PMI-based ranking
        return self.cooccurrence.get_related_by_pmi(tag_id, limit, exclude_ids)

    def get_contextual_suggestions(
        self,
        selected_tag_ids: List[int],
        limit: int = 8,
        exclude_ids: Optional[List[int]] = None
    ) -> List[Dict]:
        """Get suggestions ranked by relevance to all currently selected tags."""
        if not selected_tag_ids:
            return []
        # Stage 1: candidate generation via raw co-occurrence (fast)
        candidates = self.cooccurrence.get_contextual_suggestions(
            selected_tag_ids, limit * 3, exclude_ids
        )
        if not candidates:
            return []

        # Stage 2: re-rank with full scoring function
        scored = self._score_candidates(candidates, selected_tag_ids)
        return scored[:limit]

    def get_suggestions_for_item(self, item_id: str, limit: int = 8) -> List[Dict]:
        """Get related suggestions for an item based on its current explicit tags."""
        explicit = self.tags.get_item_tags_explicit(item_id)
        explicit_ids = [t["id"] for t in explicit]
        if not explicit_ids:
            return []
        all_current = self.tags.get_item_tags_all(item_id)
        exclude_ids = [t["id"] for t in all_current]
        return self.get_contextual_suggestions(explicit_ids, limit, exclude_ids)

    def record_feedback(self, item_id: str, context_tag_ids: List[int],
                        suggested_tag_id: int, outcome: str) -> None:
        """Store user feedback for a suggestion."""
        if self.feedback is not None:
            self.feedback.record(item_id, context_tag_ids, suggested_tag_id, outcome)
