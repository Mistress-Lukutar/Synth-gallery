'''
File:   reasoning.py
Brief:  Reasoning-effort capability detection and per-protocol payload mapping.
Author: Mistress-Lukutar
Date:   2026-10-11

Reasoning control is not uniform across providers, so the chat exposes a
per-model *capability* plus a per-user *effort choice*:

- Capability (``ai_models.reasoning_support``): what the model/server accepts.
  ``None`` = auto-detect from the model id; ``levels`` (reasoning_effort),
  ``toggle`` (thinking on/off, Z.ai/GLM style), ``enable_toggle`` (Qwen
  enable_thinking), ``none`` (explicitly no control). For the dedicated
  anthropic / google_gemini protocols the wire format is fixed by the
  protocol itself, so any non-``none`` capability just means "budget".
- Choice (``ai_chat_settings.reasoning_effort``): one of the values listed
  in ``VALID_EFFORT_VALUES`` for the effective UI style, or ``None`` for
  the provider default (nothing is sent).
'''
from __future__ import annotations

import re
from typing import Optional

# Manual capability values for ai_models.reasoning_support.
SUPPORT_LEVELS = "levels"
SUPPORT_TOGGLE = "toggle"
SUPPORT_ENABLE_TOGGLE = "enable_toggle"
SUPPORT_NONE = "none"
MANUAL_SUPPORT_VALUES = (SUPPORT_LEVELS, SUPPORT_TOGGLE, SUPPORT_ENABLE_TOGGLE, SUPPORT_NONE)

# Effective UI styles resolved from (protocol, model id, manual override).
UI_LEVELS = "levels"
UI_TOGGLE = "toggle"
UI_ENABLE_TOGGLE = "enable_toggle"
UI_BUDGET = "budget"
UI_OFF = "off"

# The effort values the composer may persist, per effective UI style.
VALID_EFFORT_VALUES: dict = {
    UI_LEVELS: ("minimal", "low", "medium", "high"),
    UI_TOGGLE: ("on", "off"),
    UI_ENABLE_TOGGLE: ("on", "off"),
    UI_BUDGET: ("off", "low", "medium", "high"),
    UI_OFF: (),
}

# ----------------------------------------------------------------------
# Capability heuristics.
# ----------------------------------------------------------------------

# Models that expose discrete reasoning-effort levels (OpenAI-style
# reasoning_effort): gpt-5.x and the o-series (o1/o3/o4, incl. -mini/-pro).
_LEVELS_PATTERNS: tuple = (
    "gpt-5",
)

# Z.ai / GLM thinking switch: "thinking": {"type": "enabled"|"disabled"}.
_TOGGLE_PATTERNS: tuple = (
    "glm-4", "glm-5",
)

# Qwen3 hybrid thinking: "enable_thinking": true|false (DashScope & most
# OpenAI-compatible self-hosted servers).
_ENABLE_TOGGLE_PATTERNS: tuple = (
    "qwen3",
)

# Anthropic extended thinking (budget_tokens) exists on Claude 3.7+.
# Ids come in both shapes: claude-3-7-sonnet / claude-4-... and the newer
# claude-sonnet-4-5 / claude-opus-4-1 ordering.
_ANTHROPIC_BUDGET_PATTERNS: tuple = (
    "claude-3-7", "claude-4", "claude-5",
    "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
)

# Gemini thinkingConfig exists on 2.5+ (non-lite thinking budget, 3.x level).
_GEMINI_BUDGET_PATTERNS: tuple = (
    "gemini-2.5", "gemini-3",
)

# Standalone o-series segment: "o1", "o3-mini", "o4-mini", but not "gpt-4o".
_O_SERIES_RE = re.compile(r"(^|[^a-z0-9])o[134]($|[^a-z0-9])")


def guess_support(model_id: str, protocol: str = "openai_compatible") -> str:
    '''Best-effort reasoning capability from the model id (case-insensitive).

    Returns one of UI_LEVELS / UI_TOGGLE / UI_ENABLE_TOGGLE / UI_BUDGET /
    UI_OFF. Conservative on purpose: unknown models get UI_OFF (nothing is
    ever sent), and a manual override in Settings wins over any guess.
    '''
    lowered = (model_id or "").lower()
    if protocol == "anthropic":
        if any(kw in lowered for kw in _ANTHROPIC_BUDGET_PATTERNS):
            return UI_BUDGET
        return UI_OFF
    if protocol == "google_gemini":
        if any(kw in lowered for kw in _GEMINI_BUDGET_PATTERNS):
            return UI_BUDGET
        return UI_OFF
    if any(kw in lowered for kw in _LEVELS_PATTERNS) or _O_SERIES_RE.search(lowered):
        return UI_LEVELS
    if any(kw in lowered for kw in _TOGGLE_PATTERNS):
        return UI_TOGGLE
    if any(kw in lowered for kw in _ENABLE_TOGGLE_PATTERNS):
        return UI_ENABLE_TOGGLE
    return UI_OFF


def effective_ui(protocol: str, model_id: str, manual_support: Optional[str]) -> str:
    '''Resolve the effective UI style for a model.

    manual_support: the raw ai_models.reasoning_support column (None/'' =
    auto). For the dedicated anthropic / google_gemini protocols the wire
    format is dictated by the protocol, so any non-'none' manual value
    still resolves to UI_BUDGET.
    '''
    if manual_support == SUPPORT_NONE:
        return UI_OFF
    if manual_support in (SUPPORT_LEVELS, SUPPORT_TOGGLE, SUPPORT_ENABLE_TOGGLE):
        if protocol == "openai_compatible":
            return manual_support
        return UI_BUDGET
    return guess_support(model_id, protocol)


def normalize_effort(ui: str, value: Optional[str]) -> Optional[str]:
    '''Return ``value`` if it is a valid effort for the style, else None.

    A stored choice that does not fit the active model (e.g. "on" after
    switching from a toggle model to a levels model) degrades to the
    provider default instead of breaking the request.
    '''
    if value is None:
        return None
    return value if value in VALID_EFFORT_VALUES.get(ui, ()) else None


# ----------------------------------------------------------------------
# Per-protocol payload mapping.
# ----------------------------------------------------------------------

def openai_reasoning_params(ui: str, value: Optional[str]) -> dict:
    '''Extra request-body fields for the OpenAI-compatible protocol.

    Returns {} for the provider default so strict servers never see
    unknown fields.
    '''
    value = normalize_effort(ui, value)
    if value is None:
        return {}
    if ui == UI_LEVELS:
        return {"reasoning_effort": value}
    if ui == UI_TOGGLE:
        return {"thinking": {"type": "enabled" if value == "on" else "disabled"}}
    if ui == UI_ENABLE_TOGGLE:
        return {"enable_thinking": value == "on"}
    return {}


_ANTHROPIC_BUDGETS: dict = {"low": 4096, "medium": 10240, "high": 24576}
_ANTHROPIC_MIN_BUDGET = 1024
_ANTHROPIC_DEFAULT_MAX = 8192


def anthropic_thinking_param(
    ui: str, value: Optional[str], max_tokens: Optional[int]
) -> tuple[Optional[dict], Optional[int], Optional[float]]:
    '''Thinking block + adjusted max_tokens/temperature for Anthropic.

    Anthropic requires budget_tokens >= 1024 and < max_tokens, and pins
    temperature to 1 while thinking is on. An explicit ``max_tokens`` is
    respected — the budget shrinks to fit under it (Anthropic counts
    thinking tokens toward max_tokens); with no cap set, the default
    output limit is lifted to host the full requested budget. Returns
    (thinking, max_tokens, temperature); thinking is None when nothing
    should be sent (default or "off" — omitting the block leaves thinking
    disabled).
    '''
    value = normalize_effort(ui, value)
    if ui != UI_BUDGET or value is None or value == "off":
        return None, max_tokens, None
    budget = _ANTHROPIC_BUDGETS[value]
    if max_tokens is None:
        eff_max = max(budget + _ANTHROPIC_MIN_BUDGET, _ANTHROPIC_DEFAULT_MAX)
        return {"type": "enabled", "budget_tokens": budget}, eff_max, 1.0
    eff_max = max_tokens
    budget = min(budget, eff_max - _ANTHROPIC_MIN_BUDGET)
    if budget < _ANTHROPIC_MIN_BUDGET:
        # The output cap is too small to host thinking; lift both to the
        # documented floor (budget 1024 + room for the answer).
        budget = _ANTHROPIC_MIN_BUDGET
        eff_max = budget + _ANTHROPIC_MIN_BUDGET
    return {"type": "enabled", "budget_tokens": budget}, eff_max, 1.0


# Gemini 2.5 thinking budgets (tokens). Pro models cannot disable thinking
# (budget 0 is rejected; the documented minimum is 128).
_GEMINI_BUDGETS: dict = {"off": 0, "low": 2048, "medium": 8192, "high": 24576}
_GEMINI_PRO_MIN_BUDGET = 128


def gemini_thinking_config(ui: str, value: Optional[str], model_id: str) -> Optional[dict]:
    '''generationConfig.thinkingConfig for the Gemini protocol.

    Gemini 3 switched from a token budget to discrete thinking levels
    ("low"/"high"); "off" degrades to "low" there because 3.x Pro cannot
    disable thinking.
    '''
    value = normalize_effort(ui, value)
    if ui != UI_BUDGET or value is None:
        return None
    lowered = (model_id or "").lower()
    if lowered.startswith("gemini-3"):
        level = "high" if value == "high" else "low"
        return {"thinkingConfig": {"thinkingLevel": level}}
    budget = _GEMINI_BUDGETS[value]
    if budget == 0 and "pro" in lowered:
        budget = _GEMINI_PRO_MIN_BUDGET
    return {"thinkingConfig": {"thinkingBudget": budget}}
