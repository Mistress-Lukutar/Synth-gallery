'''
File:   test_types.py
Brief:  Unit tests for LLM type heuristics and defaults.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

from app.infrastructure.services.llm.types import (
    PROTOCOL_DEFAULT_BASE_URLS,
    AiProtocol,
    guess_model_capabilities,
)


def test_gpt_4o_supports_tools_and_vision():
    assert guess_model_capabilities("gpt-4o") == (True, True)


def test_text_embedding_model_has_no_tools():
    tools, vision = guess_model_capabilities("text-embedding-3-large")
    assert tools is False


def test_qwen2_vl_supports_vision():
    assert guess_model_capabilities("Qwen2-VL-7B-Instruct") == (True, True)


def test_claude_3_5_sonnet_supports_tools_and_vision():
    assert guess_model_capabilities("claude-3-5-sonnet-20241022") == (True, True)


def test_whisper_has_no_tools():
    tools, _ = guess_model_capabilities("whisper-1")
    assert tools is False


def test_matching_is_case_insensitive():
    assert guess_model_capabilities("GPT-4O")[0] is True
    tools, _ = guess_model_capabilities("Text-Embedding-3-Small")
    assert tools is False


def test_plain_chat_model_defaults():
    assert guess_model_capabilities("llama-3-70b-instruct") == (True, False)


def test_protocol_default_base_urls():
    assert (
        PROTOCOL_DEFAULT_BASE_URLS[AiProtocol.OPENAI_COMPATIBLE.value]
        == "https://api.openai.com/v1"
    )
    assert (
        PROTOCOL_DEFAULT_BASE_URLS[AiProtocol.ANTHROPIC.value]
        == "https://api.anthropic.com/v1"
    )
    assert (
        PROTOCOL_DEFAULT_BASE_URLS[AiProtocol.GOOGLE_GEMINI.value]
        == "https://generativelanguage.googleapis.com/v1beta"
    )
