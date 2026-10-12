"""Integration tests for the AI chat tag tools.

Tool-level tests (no LLM involved): executors are called directly with a
``ToolContext``, mirroring test_ai_tools_files.py. Covers the batch
get_tag_info (bulk resolve + implications) and the list_tags browse tool
with its category filter.
"""
import json

import pytest

from app.application.services.ai_tools import (
    TOOL_REGISTRY,
    ToolContext,
    ToolError,
)
from app.infrastructure.repositories import (
    TagImplicationRepository,
    TagsRepository,
)


def _call(name: str, args: dict, user_id: int) -> dict:
    result = TOOL_REGISTRY[name].executor(args, ToolContext(user_id=user_id))
    return json.loads(result)


@pytest.fixture(scope="function")
def categories(db_connection) -> None:
    """Fresh databases have no tag categories; create two."""
    db_connection.execute(
        "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
        "VALUES (1, 'general', 'General', '#6b7280', 1)"
    )
    db_connection.execute(
        "INSERT OR IGNORE INTO tag_categories (id, slug, name, color, sort_order) "
        "VALUES (2, 'species', 'Species', '#22c55e', 2)"
    )
    db_connection.commit()


@pytest.fixture(scope="function")
def tag_ids(db_connection, categories) -> dict:
    """red_fox / dragon / canine with one implication: red_fox -> canine."""
    repo = TagsRepository(db_connection)
    ids = {
        "red_fox": repo.create("red_fox", 1, "a fox with red fur"),
        "dragon": repo.create("dragon", 2),
        "canine": repo.create("canine", 1),
    }
    db_connection.execute(
        "UPDATE tags SET usage_count = 7 WHERE name = 'dragon'"
    )
    db_connection.commit()
    TagImplicationRepository(db_connection).create(
        ids["red_fox"], ids["canine"]
    )
    return ids


# ============================================================================
# get_tag_info (batch)
# ============================================================================

def test_get_tag_info_resolves_batch_in_one_call(
    db_connection, test_user, tag_ids
):
    result = _call(
        "get_tag_info",
        {"names": ["red_fox", "Dragon", "missing_tag", "не_тег", "red_fox"]},
        test_user["id"],
    )

    # Duplicates collapse; normalization is applied ("Dragon" -> dragon).
    assert [t["name"] for t in result["tags"]] == ["red_fox", "dragon"]
    fox, dragon = result["tags"]
    assert fox["id"] == tag_ids["red_fox"]
    assert fox["category"] == "General"
    assert fox["description"] == "a fox with red fur"
    assert fox["implies"] == ["canine"]
    # Tags without description/implications omit those keys.
    assert "description" not in dragon
    assert "implies" not in dragon
    assert dragon["usage_count"] == 7

    assert result["not_found"] == ["missing_tag"]
    # Cyrillic (outside TAG_NAME_PATTERN) is invalid, not merely missing.
    assert result["invalid"] == ["не_тег"]


def test_get_tag_info_reports_implied_by(db_connection, test_user, tag_ids):
    result = _call("get_tag_info", {"names": ["canine"]}, test_user["id"])
    assert result["tags"][0]["implied_by"] == ["red_fox"]


def test_get_tag_info_can_skip_implications(
    db_connection, test_user, tag_ids
):
    result = _call(
        "get_tag_info",
        {"names": ["red_fox", "canine"], "include_implications": False},
        test_user["id"],
    )
    assert [t["name"] for t in result["tags"]] == ["red_fox", "canine"]
    dumped = json.dumps(result)
    assert "implies" not in dumped and "implied_by" not in dumped


def test_get_tag_info_all_unknown_still_answers(db_connection, test_user, categories):
    result = _call("get_tag_info", {"names": ["ghost_tag"]}, test_user["id"])
    assert result["tags"] == []
    assert result["not_found"] == ["ghost_tag"]
    assert result["invalid"] == []


def test_get_tag_info_validates_names_argument(db_connection, test_user, categories):
    with pytest.raises(ToolError):
        _call("get_tag_info", {}, test_user["id"])
    with pytest.raises(ToolError):
        _call("get_tag_info", {"names": []}, test_user["id"])
    with pytest.raises(ToolError) as excinfo:
        _call("get_tag_info", {"names": ["a"] * 51}, test_user["id"])
    assert "batches" in str(excinfo.value)


# ============================================================================
# list_tags (browse)
# ============================================================================

def test_list_tags_orders_by_usage(db_connection, test_user, tag_ids):
    result = _call("list_tags", {}, test_user["id"])
    # dragon (7 usages) first, then the rest alphabetically.
    assert [t["name"] for t in result["tags"]][:3] == [
        "dragon", "canine", "red_fox",
    ]
    assert result["total"] == 3
    fox = next(t for t in result["tags"] if t["name"] == "red_fox")
    assert fox["usage_count"] == 0
    assert fox["description"] == "a fox with red fur"


def test_list_tags_substring_filter(db_connection, test_user, tag_ids):
    result = _call("list_tags", {"query": "fox"}, test_user["id"])
    assert [t["name"] for t in result["tags"]] == ["red_fox"]
    assert result["total"] == 1


def test_list_tags_category_filter(db_connection, test_user, tag_ids):
    by_slug = _call("list_tags", {"category": "species"}, test_user["id"])
    assert [t["name"] for t in by_slug["tags"]] == ["dragon"]

    # Category may also be given by display name.
    by_name = _call("list_tags", {"category": "Species"}, test_user["id"])
    assert [t["name"] for t in by_name["tags"]] == ["dragon"]

    with pytest.raises(ToolError) as excinfo:
        _call("list_tags", {"category": "nope"}, test_user["id"])
    assert "not found" in str(excinfo.value)
