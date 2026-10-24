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
