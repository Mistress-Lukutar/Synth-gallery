'''
File:   __init__.py
Brief:  AI tools package - tools the built-in chat model may call.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

``TOOL_REGISTRY`` maps tool names to :class:`ToolDef` entries;
``TOOL_SPECS`` is the provider-agnostic list advertised with every
``ChatRequest``.
'''
from ....infrastructure.services.llm.types import ToolSpec
from .base import ToolContext, ToolDef, ToolError, VisionRequestSignal
from .registry import MEMORY_TOOL_NAMES, TOOL_REGISTRY

# Tool specifications handed to the LLM with every request.
TOOL_SPECS: list = [
    ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.parameters_json_schema,
    )
    for tool in TOOL_REGISTRY.values()
]

__all__ = [
    "MEMORY_TOOL_NAMES",
    "TOOL_REGISTRY",
    "TOOL_SPECS",
    "ToolContext",
    "ToolDef",
    "ToolError",
    "VisionRequestSignal",
]
