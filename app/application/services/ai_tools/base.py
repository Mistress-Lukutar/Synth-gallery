"""AI tool foundation - context, errors and tool definitions.

Every tool executor receives a :class:`ToolContext` (the requesting user)
and the parsed arguments dict, and returns a plain-text result for the
model. Executors raise :class:`ToolError` when the model should see an
error result, and :class:`VisionRequestSignal` when the user must approve
image viewing first.
"""
from dataclasses import dataclass, field
from typing import Callable, List


@dataclass
class ToolContext:
    """Context handed to every tool executor."""

    user_id: int


class ToolError(Exception):
    """Tool failure surfaced to the model as an error result."""


class VisionRequestSignal(Exception):
    """Raised by ``view_images`` to pause for user approval.

    Attributes:
        item_ids: Validated media item IDs the model wants to see.
    """

    def __init__(self, item_ids: List[str]):
        self.item_ids = list(item_ids)
        super().__init__(f"vision approval requested for items {self.item_ids}")


@dataclass
class ToolDef:
    """One tool exposed to the model."""

    name: str
    description: str
    parameters_json_schema: dict
    executor: Callable[[dict, ToolContext], str]
    # Tools listed here are executed but never advertised to models whose
    # provider reports no tool support; reserved for future use.
    metadata: dict = field(default_factory=dict)
