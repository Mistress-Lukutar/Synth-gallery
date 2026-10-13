"""AI agent service - CRUD, validation and built-in seeding.

Agents are per-user rows in ``ai_agents``: a system prompt plus an
explicit tool allow-list. Three built-in agents (Tagger, Librarian,
Curator) are seeded idempotently per user from :data:`BUILTIN_AGENTS`;
re-seeding on every list call rolls prompt improvements out to existing
installations while keeping row ids stable.
"""
import json
from typing import List

from ...logging_config import get_logger
from .ai_tools import TOOL_REGISTRY

logger = get_logger(__name__)

# Preset icon names / palette size shared with the frontend
# (app/static/js/ai-agents-icons.js must stay in sync).
AGENT_ICONS = (
    "sparkles",
    "tag",
    "book",
    "brush",
    "wrench",
    "folder",
    "image",
    "note",
    "star",
    "search",
)
AGENT_COLOR_COUNT = 6

MAX_AGENTS_NAME_LENGTH = 64
MAX_AGENTS_DESCRIPTION_LENGTH = 200
MAX_SYSTEM_PROMPT_LENGTH = 20_000


class AgentValidationError(ValueError):
    """Invalid agent payload (400)."""


class AgentNotFoundError(Exception):
    """Agent does not exist or belongs to another user (404)."""


class AgentPermissionError(Exception):
    """Built-in agents cannot be edited or deleted (403)."""


TAGGER_PROMPT = """\
You are Tagger, a meticulous tagging assistant inside Synth Gallery, the \
user's personal media vault. Your task: assign precise, descriptive tags \
to the items the user asks about.

Workflow for each item:
- Look at the image with view_images (at most 8 images per call) and read \
its embedded metadata with get_item_metadata. The generation prompt stored \
in the file (for example the "Prompt" PNG text chunk) usually names the \
subject, the character and the style - mine it first.
- Study how similar items are tagged: search_items by tags suggested by the \
metadata (character name, series, style), inspect the results with get_item \
and reuse the established vocabulary. get_tag_suggestions gives statistical \
hints based on what the user tagged before.
- Verify all candidate tags for an item with a single get_tag_info call, \
passing every name in `names` at once; use list_tags only to discover tags \
by substring or category. NEVER create new tags: if a fitting tag does not \
exist, skip it and report the missing tags to the user at the end of your \
answer. When a tag's meaning is unclear, check its booru entry first with \
fetch_url (for example https://danbooru.donmai.us/wiki_pages/<tag>.json) \
before deciding it does not fit.
- Apply tags with add_item_tags. Tag implications resolve automatically - \
never add implied tags yourself.
- Never change titles or descriptions.
- Explain what you saw and why each tag fits. Describe every relevant aspect \
of the image in detail: subject, pose, clothing, setting, style, quality.\
"""

LIBRARIAN_PROMPT = """\
You are Librarian, a read-only research assistant inside Synth Gallery, the \
user's personal encrypted media vault. You can search and inspect the \
library, but you cannot modify anything.
- Answer questions about the library: find items, summarize folders and \
albums, explain how content is tagged, and report audit results (untagged, \
untitled or broken items).
- Quote concrete item ids and titles so the user can act on your findings.
- When the answer requires a change (tagging, renaming, editing), describe \
what you would change and suggest switching to an agent with write access.\
"""

CURATOR_PROMPT = """\
You are Curator, a metadata caretaker inside Synth Gallery.
You fix titles, descriptions and tags together with the user.
- Inspect items first (get_item, get_item_metadata, view_images) before \
proposing any change.
- Apply changes with update_item, add_item_tags and remove_item_tags. Every \
change is shown to the user for confirmation before it runs, so propose \
edits in small, reviewable steps.
- You may create new tags with create_tags, but only when the user clearly \
wants a tag that does not exist - name the exact tags you are about to \
create first.
- Tag implications resolve automatically - never add implied tags yourself.
- When writing or checking tag descriptions, fetch_url on a booru wiki \
(danbooru wiki_pages/<tag>.json, e621 wiki_pages?title=<tag>) grounds the \
text in what the tag actually means instead of guesswork.
- Always end with a compact before/after summary of every change you made.
- Never guess: when data is ambiguous, ask the user.\
"""


BUILTIN_AGENTS: List[dict] = [
    {
        "builtin_key": "tagger",
        "name": "Tagger",
        "description": "Tags media from what it sees and from embedded prompts",
        "system_prompt": TAGGER_PROMPT,
        "icon": "tag",
        "color_index": 1,
        "sort_order": 0,
        "tools": [
            "search_items",
            "list_folder",
            "get_item",
            "get_item_metadata",
            "get_tag_suggestions",
            "list_tags",
            "get_tag_info",
            "view_images",
            "add_item_tags",
            "fetch_url",
            "web_search",
        ],
    },
    {
        "builtin_key": "librarian",
        "name": "Librarian",
        "description": "Read-only guide to your library",
        "system_prompt": LIBRARIAN_PROMPT,
        "icon": "book",
        "color_index": 0,
        "sort_order": 1,
        "tools": [
            "search_items",
            "list_folder",
            "list_folders",
            "list_albums",
            "get_item",
            "get_item_metadata",
            "read_note",
            "list_tags",
            "get_tag_info",
            "get_tag_suggestions",
            "audit_library",
            "view_images",
            "fetch_url",
            "web_search",
        ],
    },
    {
        "builtin_key": "curator",
        "name": "Curator",
        "description": "Cleans up titles, descriptions and tags with your approval",
        "system_prompt": CURATOR_PROMPT,
        "icon": "brush",
        "color_index": 2,
        "sort_order": 2,
        "tools": [
            "search_items",
            "list_folder",
            "get_item",
            "get_item_metadata",
            "list_tags",
            "get_tag_info",
            "get_tag_suggestions",
            "add_item_tags",
            "remove_item_tags",
            "create_tags",
            "update_item",
            "set_tag_descriptions",
            "fetch_url",
            "web_search",
        ],
    },
]


class AiAgentService:
    """Agent CRUD on top of :class:`AiAgentRepository` with validation."""

    def __init__(self, agent_repo):
        """Create the service.

        Args:
            agent_repo: AiAgentRepository instance.
        """
        self.agent_repo = agent_repo

    # =====================================================================
    # Seeding + reads
    # =====================================================================

    def list_agents(self, user_id: int) -> List[dict]:
        """List the user's agents, seeding built-ins first."""
        self._ensure_builtin_agents(user_id)
        return self.agent_repo.list_for_user(user_id)

    def get_agent(self, user_id: int, agent_id: int) -> dict:
        """Return an owned agent or raise :class:`AgentNotFoundError`."""
        agent = self.agent_repo.get_by_id(agent_id, user_id)
        if agent is None:
            raise AgentNotFoundError(f"agent {agent_id} not found")
        return agent

    def _ensure_builtin_agents(self, user_id: int) -> None:
        """Upsert built-in agents for the user by ``builtin_key``."""
        for definition in BUILTIN_AGENTS:
            row = self.agent_repo.get_by_builtin_key(
                user_id, definition["builtin_key"]
            )
            fields = {
                "name": definition["name"],
                "description": definition["description"],
                "system_prompt": definition["system_prompt"],
                "icon": definition["icon"],
                "color_index": definition["color_index"],
                "tools_json": _dump_tools(definition["tools"]),
                "sort_order": definition["sort_order"],
            }
            if row is None:
                self.agent_repo.create(
                    user_id=user_id,
                    name=definition["name"],
                    description=definition["description"],
                    system_prompt=definition["system_prompt"],
                    icon=definition["icon"],
                    color_index=definition["color_index"],
                    tools=definition["tools"],
                    builtin_key=definition["builtin_key"],
                    is_builtin=True,
                    sort_order=definition["sort_order"],
                )
            elif any(row.get(key) != value for key, value in fields.items()):
                self.agent_repo.update(row["id"], user_id, fields)

    # =====================================================================
    # Mutations
    # =====================================================================

    def create_agent(self, user_id: int, fields: dict) -> dict:
        """Create a custom agent from validated request fields."""
        validated = dict(self._validate_fields(fields, partial=False))
        # create() takes the allow-list as a plain list; update() writes
        # the serialized column value.
        validated["tools"] = json.loads(validated.pop("tools_json", "[]"))
        agent_id = self.agent_repo.create(user_id=user_id, **validated)
        return self.agent_repo.get_by_id(agent_id, user_id)

    def update_agent(self, user_id: int, agent_id: int, fields: dict) -> dict:
        """Update a custom agent; built-ins are immutable."""
        agent = self.get_agent(user_id, agent_id)
        if agent.get("is_builtin"):
            raise AgentPermissionError("Built-in agents cannot be edited")
        validated = self._validate_fields(fields, partial=True)
        if validated:
            self.agent_repo.update(agent_id, user_id, validated)
        return self.agent_repo.get_by_id(agent_id, user_id)

    def delete_agent(self, user_id: int, agent_id: int) -> None:
        """Delete a custom agent and detach it from conversations/settings."""
        agent = self.get_agent(user_id, agent_id)
        if agent.get("is_builtin"):
            raise AgentPermissionError("Built-in agents cannot be deleted")
        self.agent_repo.delete(agent_id, user_id)
        self.agent_repo.clear_agent_references(agent_id, user_id)

    def duplicate_agent(self, user_id: int, agent_id: int) -> dict:
        """Copy any agent (including built-ins) into a new custom agent."""
        agent = self.get_agent(user_id, agent_id)
        name = f"Copy of {agent['name']}"[:MAX_AGENTS_NAME_LENGTH]
        new_id = self.agent_repo.create(
            user_id=user_id,
            name=name,
            description=agent.get("description", ""),
            system_prompt=agent.get("system_prompt", ""),
            icon=agent.get("icon", "sparkles"),
            color_index=agent.get("color_index", 0),
            tools=list(agent.get("tools") or []),
            sort_order=100,
        )
        return self.agent_repo.get_by_id(new_id, user_id)

    # =====================================================================
    # Validation
    # =====================================================================

    def _validate_fields(self, fields: dict, partial: bool) -> dict:
        """Validate request fields into repository-ready columns.

        Args:
            fields: Raw request fields.
            partial: True for updates (only provided keys are checked);
                False for creation (name and tools are required).

        Raises:
            AgentValidationError: On any invalid value.
        """
        validated: dict = {}

        if "name" in fields or not partial:
            name = (fields.get("name") or "").strip()
            if not name:
                raise AgentValidationError("Agent name must not be empty")
            if len(name) > MAX_AGENTS_NAME_LENGTH:
                raise AgentValidationError(
                    f"Agent name must be at most {MAX_AGENTS_NAME_LENGTH} characters"
                )
            validated["name"] = name

        if "description" in fields or not partial:
            description = (fields.get("description") or "").strip()
            if len(description) > MAX_AGENTS_DESCRIPTION_LENGTH:
                raise AgentValidationError(
                    "Agent description must be at most "
                    f"{MAX_AGENTS_DESCRIPTION_LENGTH} characters"
                )
            validated["description"] = description

        if "system_prompt" in fields or not partial:
            prompt = (fields.get("system_prompt") or "").strip()
            if len(prompt) > MAX_SYSTEM_PROMPT_LENGTH:
                raise AgentValidationError(
                    "System prompt must be at most "
                    f"{MAX_SYSTEM_PROMPT_LENGTH} characters"
                )
            validated["system_prompt"] = prompt

        if "icon" in fields or not partial:
            icon = fields.get("icon") or "sparkles"
            if icon not in AGENT_ICONS:
                raise AgentValidationError(f"Unknown icon: {icon}")
            validated["icon"] = icon

        if "color_index" in fields or not partial:
            color_index = fields.get("color_index")
            if color_index is None:
                color_index = 0
            if not isinstance(color_index, int) or not (
                0 <= color_index < AGENT_COLOR_COUNT
            ):
                raise AgentValidationError(
                    f"color_index must be an integer in 0..{AGENT_COLOR_COUNT - 1}"
                )
            validated["color_index"] = color_index

        if "tools" in fields or not partial:
            tools = fields.get("tools")
            if tools is None:
                tools = []
            if not isinstance(tools, list) or not all(
                isinstance(tool, str) for tool in tools
            ):
                raise AgentValidationError("tools must be a list of tool names")
            unknown = [t for t in tools if t not in TOOL_REGISTRY]
            if unknown:
                raise AgentValidationError(
                    "Unknown tools: " + ", ".join(sorted(unknown))
                )
            # Deduplicate while preserving order.
            validated["tools_json"] = _dump_tools(list(dict.fromkeys(tools)))

        return validated


def _dump_tools(tools: List[str]) -> str:
    """Serialize an allow-list for the ``tools_json`` column."""
    return json.dumps(list(tools), ensure_ascii=False)
