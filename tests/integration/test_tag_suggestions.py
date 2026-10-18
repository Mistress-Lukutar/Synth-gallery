"""Integration tests for tag suggestions and suggestion feedback.

Covers the per-item rejection blocklist: a tag the user rejected in the
suggestions panel never reappears in suggestions for that item, so the
panel refills with the next-best candidates instead.
"""
import pytest

from app.application.services.tag_service import TagService
from app.application.services.tag_suggestion_service import TagSuggestionService
from app.infrastructure.repositories import (
    ItemRepository,
    TagCooccurrenceRepository,
    TagFeedbackRepository,
    TagImplicationRepository,
    TagMutexRepository,
    TagsRepository,
)


@pytest.fixture(scope="function")
def categories(db_connection) -> None:
    """Fresh databases have no tag categories; create one."""
    db_connection.execute(
        "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
        "VALUES (1, 'general', 'General', '#6b7280', 1)"
    )
    db_connection.commit()


@pytest.fixture(scope="function")
def tag_ids(db_connection, categories) -> dict:
    repo = TagsRepository(db_connection)
    return {
        "canine": repo.create("canine", 1),
        "red_fox": repo.create("red_fox", 1),
        "dragon": repo.create("dragon", 1),
        "forest": repo.create("forest", 1),
    }


def _make_item(db_connection, test_user, test_folder) -> str:
    return ItemRepository(db_connection).create(
        item_type="media",
        folder_id=test_folder,
        user_id=test_user["id"],
        title="img.png",
    )


def _suggestion_service(db_connection) -> TagSuggestionService:
    return TagSuggestionService(
        cooccurrence_repo=TagCooccurrenceRepository(db_connection),
        tags_repo=TagsRepository(db_connection),
        mutex_repo=TagMutexRepository(db_connection),
        feedback_repo=TagFeedbackRepository(db_connection),
    )


def _tag_service(db_connection) -> TagService:
    return TagService(
        TagsRepository(db_connection),
        TagImplicationRepository(db_connection),
        TagCooccurrenceRepository(db_connection),
        TagMutexRepository(db_connection),
    )


def _seed_cooccurrence(db_connection, tag_ids) -> None:
    """canine co-occurs with red_fox > dragon > forest."""
    TagCooccurrenceRepository(db_connection).update_deltas(
        [
            (tag_ids["canine"], tag_ids["red_fox"]),
            (tag_ids["canine"], tag_ids["dragon"]),
            (tag_ids["canine"], tag_ids["forest"]),
        ],
        [],
    )


# ============================================================================
# Repository
# ============================================================================

def test_rejected_ids_are_scoped_to_item(db_connection, tag_ids):
    repo = TagFeedbackRepository(db_connection)
    repo.record("item-a", [tag_ids["canine"]], tag_ids["dragon"], "rejected")
    repo.record("item-a", [tag_ids["canine"]], tag_ids["red_fox"], "accepted")
    repo.record("item-b", [tag_ids["canine"]], tag_ids["dragon"], "rejected")
    # 'dismissed' is a valid outcome but is not a rejection blocklist entry.
    repo.record("item-a", [tag_ids["canine"]], tag_ids["forest"], "dismissed")

    assert repo.get_rejected_tag_ids_for_item("item-a") == [tag_ids["dragon"]]
    assert repo.get_rejected_tag_ids_for_item("item-b") == [tag_ids["dragon"]]
    assert repo.get_rejected_tag_ids_for_item("item-c") == []


# ============================================================================
# Service
# ============================================================================

def test_suggestions_exclude_rejected_tags(
    db_connection, test_user, test_folder, tag_ids
):
    item_id = _make_item(db_connection, test_user, test_folder)
    _tag_service(db_connection).add_tag_to_item(item_id, tag_ids["canine"])
    _seed_cooccurrence(db_connection, tag_ids)
    service = _suggestion_service(db_connection)

    names = [t["name"] for t in service.get_suggestions_for_item(item_id)]
    assert set(names) == {"red_fox", "dragon", "forest"}

    service.record_feedback(
        item_id, [tag_ids["canine"]], tag_ids["dragon"], "rejected"
    )

    names = [t["name"] for t in service.get_suggestions_for_item(item_id)]
    assert set(names) == {"red_fox", "forest"}


def test_accepted_feedback_does_not_exclude(
    db_connection, test_user, test_folder, tag_ids
):
    item_id = _make_item(db_connection, test_user, test_folder)
    _tag_service(db_connection).add_tag_to_item(item_id, tag_ids["canine"])
    _seed_cooccurrence(db_connection, tag_ids)
    service = _suggestion_service(db_connection)

    service.record_feedback(
        item_id, [tag_ids["canine"]], tag_ids["forest"], "accepted"
    )

    names = [t["name"] for t in service.get_suggestions_for_item(item_id)]
    assert set(names) == {"red_fox", "dragon", "forest"}


# ============================================================================
# Routes
# ============================================================================

def test_suggestions_endpoint_excludes_after_feedback(
    authenticated_client, csrf_token, db_connection, test_user, test_folder,
    tag_ids,
):
    item_id = _make_item(db_connection, test_user, test_folder)
    _tag_service(db_connection).add_tag_to_item(item_id, tag_ids["canine"])
    _seed_cooccurrence(db_connection, tag_ids)

    resp = authenticated_client.get(f"/api/items/{item_id}/tags/suggestions")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["tags"]]
    assert set(names) == {"red_fox", "dragon", "forest"}

    resp = authenticated_client.post(
        "/api/tag-feedback",
        json={
            "item_id": item_id,
            "suggested_tag_id": tag_ids["dragon"],
            "outcome": "rejected",
            "context_tag_ids": [tag_ids["canine"]],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200

    resp = authenticated_client.get(f"/api/items/{item_id}/tags/suggestions")
    names = [t["name"] for t in resp.json()["tags"]]
    assert set(names) == {"red_fox", "forest"}


def test_feedback_endpoint_validates_outcome(
    authenticated_client, csrf_token, db_connection, test_user, test_folder,
    tag_ids,
):
    item_id = _make_item(db_connection, test_user, test_folder)
    resp = authenticated_client.post(
        "/api/tag-feedback",
        json={
            "item_id": item_id,
            "suggested_tag_id": tag_ids["dragon"],
            "outcome": "bogus",
            "context_tag_ids": [],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 400
