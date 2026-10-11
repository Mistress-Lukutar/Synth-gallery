"""Unit tests for the reasoning-effort helper module.

Covers capability heuristics (model-id patterns per protocol), the
per-protocol payload mapping (OpenAI-compatible params, Anthropic thinking
block, Gemini thinkingConfig) and the effort normalization rules.
"""
import pytest

from app.infrastructure.services.llm import reasoning as r


# ============================================================================
# Capability heuristics
# ============================================================================

@pytest.mark.parametrize("model_id,expected", [
    ("gpt-5.1", r.UI_LEVELS),
    ("gpt-5-mini-2025-08-07", r.UI_LEVELS),
    ("o3", r.UI_LEVELS),
    ("o3-mini", r.UI_LEVELS),
    ("o4-mini", r.UI_LEVELS),
    ("o1-preview", r.UI_LEVELS),
    ("glm-4.6", r.UI_TOGGLE),
    ("glm-4.5-air", r.UI_TOGGLE),
    ("glm-5", r.UI_TOGGLE),
    ("qwen3-235b-a22b", r.UI_ENABLE_TOGGLE),
    ("deepseek-r1", r.UI_OFF),      # reasoning always on, no control
    ("gpt-4o", r.UI_OFF),
    ("llama-3-70b", r.UI_OFF),
    ("m-1", r.UI_OFF),              # opaque ids stay off (send nothing)
])
def test_guess_support_openai_compatible(model_id, expected):
    assert r.guess_support(model_id) == expected


def test_guess_support_o_series_word_boundaries():
    """'gpt-4o' must not match the o-series pattern ('o' without a digit)."""
    assert r.guess_support("gpt-4o") == r.UI_OFF
    assert r.guess_support("grok-o1") == r.UI_LEVELS  # segment "o1" standalone


@pytest.mark.parametrize("model_id,expected", [
    ("claude-sonnet-4-5", r.UI_BUDGET),
    ("claude-opus-4-1", r.UI_BUDGET),
    ("claude-3-7-sonnet-latest", r.UI_BUDGET),
    ("claude-3-5-sonnet-latest", r.UI_OFF),  # pre-3.7 has no thinking
    ("claude-2.1", r.UI_OFF),
])
def test_guess_support_anthropic(model_id, expected):
    assert r.guess_support(model_id, "anthropic") == expected


@pytest.mark.parametrize("model_id,expected", [
    ("gemini-2.5-flash", r.UI_BUDGET),
    ("gemini-2.5-pro", r.UI_BUDGET),
    ("gemini-3-pro-preview", r.UI_BUDGET),
    ("gemini-1.5-pro", r.UI_OFF),
    ("gemini-2.0-flash-lite", r.UI_OFF),
])
def test_guess_support_gemini(model_id, expected):
    assert r.guess_support(model_id, "google_gemini") == expected


def test_effective_ui_manual_override():
    assert r.effective_ui("openai_compatible", "m-1", "levels") == r.UI_LEVELS
    assert r.effective_ui("openai_compatible", "gpt-5", "none") == r.UI_OFF
    # Heuristic wins when the model auto-detects.
    assert r.effective_ui("openai_compatible", "glm-4.6", None) == r.UI_TOGGLE
    assert r.effective_ui("openai_compatible", "m-1", None) == r.UI_OFF


def test_effective_ui_protocol_dictates_wire_format():
    """Manual 'levels'/'toggle' on the dedicated protocols still resolves to
    the budget style — only 'none' disables."""
    assert r.effective_ui("anthropic", "claude-3-5-sonnet", "levels") == r.UI_BUDGET
    assert r.effective_ui("google_gemini", "gpt-5", "toggle") == r.UI_BUDGET
    assert r.effective_ui("anthropic", "claude-sonnet-4-5", None) == r.UI_BUDGET
    assert r.effective_ui("anthropic", "gpt-4o", "none") == r.UI_OFF


def test_normalize_effort_degrades_mismatched_choice():
    assert r.normalize_effort(r.UI_LEVELS, "high") == "high"
    assert r.normalize_effort(r.UI_LEVELS, "on") is None
    assert r.normalize_effort(r.UI_TOGGLE, "on") == "on"
    assert r.normalize_effort(r.UI_BUDGET, "off") == "off"
    assert r.normalize_effort(r.UI_OFF, "high") is None
    assert r.normalize_effort(r.UI_LEVELS, None) is None


# ============================================================================
# OpenAI-compatible payload mapping
# ============================================================================

def test_openai_params_default_sends_nothing():
    assert r.openai_reasoning_params(r.UI_LEVELS, None) == {}
    assert r.openai_reasoning_params(r.UI_OFF, "high") == {}


def test_openai_params_levels():
    assert r.openai_reasoning_params(r.UI_LEVELS, "minimal") == {
        "reasoning_effort": "minimal"
    }
    assert r.openai_reasoning_params(r.UI_LEVELS, "high") == {
        "reasoning_effort": "high"
    }


def test_openai_params_toggles():
    assert r.openai_reasoning_params(r.UI_TOGGLE, "on") == {
        "thinking": {"type": "enabled"}
    }
    assert r.openai_reasoning_params(r.UI_TOGGLE, "off") == {
        "thinking": {"type": "disabled"}
    }
    assert r.openai_reasoning_params(r.UI_ENABLE_TOGGLE, "on") == {
        "enable_thinking": True
    }
    assert r.openai_reasoning_params(r.UI_ENABLE_TOGGLE, "off") == {
        "enable_thinking": False
    }


# ============================================================================
# Anthropic thinking block
# ============================================================================

def test_anthropic_default_and_off_send_nothing():
    assert r.anthropic_thinking_param(r.UI_BUDGET, None, 8192) == (None, 8192, None)
    assert r.anthropic_thinking_param(r.UI_BUDGET, "off", 8192) == (None, 8192, None)


def test_anthropic_thinking_on_lifts_default_max():
    """With no output cap set, the default 8192 lifts to host the full
    high budget; temperature pins to 1."""
    thinking, max_tokens, temperature = r.anthropic_thinking_param(
        r.UI_BUDGET, "high", None
    )
    assert thinking == {"type": "enabled", "budget_tokens": 24576}
    assert max_tokens == 24576 + 1024
    assert temperature == 1.0


def test_anthropic_thinking_respects_explicit_max_tokens():
    """An explicit cap wins: the budget shrinks to fit under it."""
    thinking, max_tokens, temperature = r.anthropic_thinking_param(
        r.UI_BUDGET, "high", 8192
    )
    assert thinking == {"type": "enabled", "budget_tokens": 8192 - 1024}
    assert max_tokens == 8192
    assert temperature == 1.0


def test_anthropic_thinking_uses_default_max_when_unset_for_low():
    thinking, max_tokens, _temperature = r.anthropic_thinking_param(
        r.UI_BUDGET, "low", None
    )
    assert thinking == {"type": "enabled", "budget_tokens": 4096}
    assert max_tokens == 8192  # the default is roomy enough already


def test_anthropic_thinking_respects_minimum_budget():
    thinking, max_tokens, _temperature = r.anthropic_thinking_param(
        r.UI_BUDGET, "low", 2000  # max - 1024 < 1024 -> budget pinned to 1024
    )
    assert thinking == {"type": "enabled", "budget_tokens": 1024}
    assert max_tokens >= 2048


# ============================================================================
# Gemini thinkingConfig
# ============================================================================

def test_gemini_default_sends_nothing():
    assert r.gemini_thinking_config(r.UI_BUDGET, None, "gemini-2.5-flash") is None
    assert r.gemini_thinking_config(r.UI_OFF, "low", "gemini-2.5-flash") is None


def test_gemini_budget_mapping():
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "low", "gemini-2.5-flash"
    ) == {"thinkingConfig": {"thinkingBudget": 2048}}
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "high", "gemini-2.5-flash"
    ) == {"thinkingConfig": {"thinkingBudget": 24576}}


def test_gemini_off_budget_and_pro_minimum():
    """Flash may disable thinking (0); Pro rejects 0 and gets the 128 floor."""
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "off", "gemini-2.5-flash"
    ) == {"thinkingConfig": {"thinkingBudget": 0}}
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "off", "gemini-2.5-pro"
    ) == {"thinkingConfig": {"thinkingBudget": 128}}


def test_gemini_3_uses_thinking_levels():
    """Gemini 3 replaced the budget with discrete levels; 'off' degrades to
    the minimum level because 3.x Pro cannot disable thinking."""
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "high", "gemini-3-pro-preview"
    ) == {"thinkingConfig": {"thinkingLevel": "high"}}
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "medium", "gemini-3-flash"
    ) == {"thinkingConfig": {"thinkingLevel": "low"}}
    assert r.gemini_thinking_config(
        r.UI_BUDGET, "off", "gemini-3-pro"
    ) == {"thinkingConfig": {"thinkingLevel": "low"}}
