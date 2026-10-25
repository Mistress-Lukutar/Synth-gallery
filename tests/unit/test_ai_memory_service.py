"""Unit tests for the AI memory service helpers (no database needed)."""
import asyncio

import pytest

from app.application.services import ai_memory_service as memory


class TestFrontmatter:
    def test_build_and_parse_roundtrip(self):
        text = memory.build_frontmatter("chat-img-tool", "image tooling") \
            + "Body line.\n"
        meta, body = memory.parse_frontmatter(text)
        assert meta == {
            "name": "chat-img-tool",
            "description": "image tooling",
        }
        assert body == "Body line.\n"

    def test_parse_without_frontmatter(self):
        meta, body = memory.parse_frontmatter("# Just a note\n")
        assert meta is None
        assert body == "# Just a note\n"

    def test_parse_preserves_extra_keys(self):
        text = "---\nname: x\ndescription: d\nsource: user\n---\n\nbody"
        meta, body = memory.parse_frontmatter(text)
        assert meta["source"] == "user"
        assert meta["name"] == "x"
        assert body == "body"

    def test_parse_body_containing_dashes_line(self):
        text = "---\nname: x\ndescription: d\n---\n\n--- rule ---\n"
        meta, body = memory.parse_frontmatter(text)
        assert meta is not None
        assert body == "--- rule ---\n"


class TestValidation:
    @pytest.mark.parametrize("name", ["alpha", "A-b_1.x", "x" * 64])
    def test_valid_names(self, name):
        assert memory.validate_section_name(name) == name

    @pytest.mark.parametrize("name", [
        "", "   ", "has space", "-leading-dash", "_underscore",
        "кوانт", "x" * 65, "MEMORY", "memory", "Memory",
    ])
    def test_invalid_names(self, name):
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_section_name(name)

    def test_description_strips_newlines(self):
        assert memory.validate_section_description("one\ntwo") == "one two"
        assert memory.validate_section_description("a\r\nb\nc") == "a b c"

    def test_description_max_length(self):
        ok = "x" * memory.MEMORY_DESCRIPTION_MAX_CHARS
        assert memory.validate_section_description(ok) == ok
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_section_description(ok + "x")

    def test_when_to_use_validated_like_description(self):
        assert memory.validate_section_when_to_use("one\ntwo") == "one two"
        ok = "x" * memory.MEMORY_WHEN_TO_USE_MAX_CHARS
        assert memory.validate_section_when_to_use(ok) == ok
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_section_when_to_use(ok + "x")
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_section_when_to_use("")

    def test_summary_validation(self):
        assert memory.validate_summary("  likes cats\n\n") == "likes cats"
        ok = "x" * memory.MEMORY_SUMMARY_MAX_CHARS
        assert memory.validate_summary(ok) == ok
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_summary(ok + "x")
        with pytest.raises(memory.MemoryValidationError):
            memory.validate_summary("   ")


class TestIndexCap:
    def test_read_index_text_truncates(self, monkeypatch):
        async def fake_read(item_id, dek):
            return "x" * (memory.MEMORY_INDEX_LIMIT + 500)

        monkeypatch.setattr(memory, "read_note_text", fake_read)
        text = asyncio.run(memory.read_index_text("item", b"k"))
        assert len(text) < memory.MEMORY_INDEX_LIMIT + 200
        assert "truncated" in text

    def test_read_index_text_passthrough(self, monkeypatch):
        async def fake_read(item_id, dek):
            return "# Memory Index\n"

        monkeypatch.setattr(memory, "read_note_text", fake_read)
        assert asyncio.run(memory.read_index_text("item", b"k")) == \
            "# Memory Index\n"


class TestFrontmatterWhenToUse:
    def test_roundtrip_with_when_to_use(self):
        text = memory.build_frontmatter(
            "prompts", "prompt writing", "when generating images"
        ) + "Body.\n"
        meta, body = memory.parse_frontmatter(text)
        assert meta == {
            "name": "prompts",
            "description": "prompt writing",
            "when_to_use": "when generating images",
        }
        assert body == "Body.\n"

    def test_without_when_to_use(self):
        text = memory.build_frontmatter("x", "d") + "b"
        meta, body = memory.parse_frontmatter(text)
        assert meta == {"name": "x", "description": "d"}
        assert body == "b"


class TestIndexEntry:
    INDEX = "# Memory Index\n\n## Summary\n\nOld summary.\n\n## Sections\n"

    def test_insert_two_line_entry(self):
        lines = memory._index_entry_block(
            "alpha", "alpha section", "renders alpha"
        )
        text = memory._upsert_index_entry(self.INDEX, "alpha", lines)
        assert (
            "- **alpha** — alpha section\n  Use when: renders alpha\n"
        ) in text

    def test_insert_without_when_to_use(self):
        lines = memory._index_entry_block("alpha", "alpha section")
        text = memory._upsert_index_entry(self.INDEX, "alpha", lines)
        assert "- **alpha** — alpha section\n" in text
        assert "Use when:" not in text

    def test_replace_removes_old_continuation_line(self):
        text = self.INDEX + \
            "- **alpha** — old description\n  Use when: old scenario\n"
        lines = memory._index_entry_block("alpha", "new", "new scenario")
        updated = memory._upsert_index_entry(text, "alpha", lines)
        assert "old description" not in updated
        assert "old scenario" not in updated
        assert "- **alpha** — new\n  Use when: new scenario\n" in updated

    def test_legacy_single_line_entry_is_replaced(self):
        text = self.INDEX + "- **alpha** — legacy line\n"
        lines = memory._index_entry_block("alpha", "new", "usage")
        updated = memory._upsert_index_entry(text, "alpha", lines)
        assert "legacy line" not in updated
        assert "  Use when: usage" in updated

    def test_remove_index_entry(self):
        text = self.INDEX + \
            "- **alpha** — a\n  Use when: u\n- **beta** — b\n"
        updated = memory.remove_index_entry(text, "alpha")
        assert "- **alpha**" not in updated
        assert "Use when: u" not in updated
        assert "- **beta** — b" in updated
        assert "## Sections" in updated

    def test_remove_unknown_entry_is_noop(self):
        assert memory.remove_index_entry(self.INDEX, "ghost") == self.INDEX


class TestIndexSummary:
    def test_replaces_summary_block(self):
        text = memory.set_index_summary(
            "# Memory Index\n\n## Summary\n\nOld.\n\n## Sections\n\n- **a** — b\n",
            "New summary.",
        )
        assert "## Summary\n\nNew summary.\n\n## Sections" in text
        assert "Old." not in text

    def test_recreates_missing_summary_heading(self):
        text = memory.set_index_summary(
            "# Memory Index\n\n## Sections\n\n- **a** — b\n",
            "Fresh.",
        )
        assert "## Summary\n\nFresh.\n\n## Sections" in text

    def test_recreates_missing_heading_and_title(self):
        text = memory.set_index_summary("- **a** — b\n", "Fresh.")
        assert text.startswith("# Memory Index\n\n## Summary\n\nFresh.\n")

    def test_summary_heading_is_last(self):
        text = memory.set_index_summary("## Summary\n", "End.")
        assert text == "## Summary\n\nEnd.\n"
