"""AI agent routes - per-user agent CRUD and the tools catalogue.

Agents are per-user rows with a system prompt and an explicit tool
allow-list; built-ins are seeded on first list and cannot be edited or
deleted (only duplicated). Mutating agent endpoints do not need the DEK
(no encrypted payloads), but the router keeps the same auth contract as
the provider settings routes.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..application.services.ai_agent_service import (
    AgentNotFoundError,
    AgentPermissionError,
    AgentValidationError,
    AiAgentService,
)
from ..application.services.ai_tools import TOOL_REGISTRY
from ..database import create_connection
from ..dependencies import require_user
from ..infrastructure.repositories.ai_agent_repository import AiAgentRepository
from ..infrastructure.repositories.ai_chat_settings_repository import (
    AiChatSettingsRepository,
)

router = APIRouter(prefix="/api/user/ai/agents", tags=["ai-agents"])


def build_ai_agent_service(db) -> AiAgentService:
    """Create AiAgentService on one connection."""
    return AiAgentService(agent_repo=AiAgentRepository(db))


# ============================================================================
# Request bodies
# ============================================================================

class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    system_prompt: str = ""
    icon: str = "sparkles"
    color_index: int = 0
    tools: List[str] = Field(default_factory=list)


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    icon: Optional[str] = None
    color_index: Optional[int] = None
    tools: Optional[List[str]] = None


class UpdateActiveAgentRequest(BaseModel):
    """Agent preselected for new chats; None switches back to Assistant."""
    active_agent_id: Optional[int] = None


# ============================================================================
# Helpers
# ============================================================================

def _tools_catalogue() -> List[dict]:
    """The tool registry as data for the agent editor UI."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "confirmation": tool.confirmation,
        }
        for tool in TOOL_REGISTRY.values()
    ]


# ============================================================================
# Agents CRUD
# ============================================================================

# ============================================================================
# Active agent selection (preselected for new chats)
# ============================================================================

@router.put("/active")
def update_active_agent(request: Request, data: UpdateActiveAgentRequest):
    """Set the agent preselected for new chats (None = Assistant).

    Declared before ``/{agent_id}`` so "active" is not parsed as an id.
    """
    user = require_user(request)

    db = create_connection()
    try:
        agent_repo = AiAgentRepository(db)
        if data.active_agent_id is not None and agent_repo.get_by_id(
            data.active_agent_id, user["id"]
        ) is None:
            raise HTTPException(status_code=400, detail="Unknown agent")
        settings_repo = AiChatSettingsRepository(db)
        settings_repo.ensure(user["id"])
        settings_repo.update(
            user["id"], {"active_agent_id": data.active_agent_id}
        )
        return {"settings": settings_repo.ensure(user["id"])}
    finally:
        db.close()


@router.get("")
def list_agents(request: Request):
    """List the user's agents (seeding built-ins) plus the tools catalogue."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_agent_service(db)
        agents = service.list_agents(user["id"])
        return {"agents": agents, "tools": _tools_catalogue()}
    finally:
        db.close()


@router.post("")
def create_agent(request: Request, data: CreateAgentRequest):
    """Create a custom agent."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_agent_service(db)
        agent = service.create_agent(
            user["id"],
            {
                "name": data.name,
                "description": data.description,
                "system_prompt": data.system_prompt,
                "icon": data.icon,
                "color_index": data.color_index,
                "tools": data.tools,
            },
        )
        return {"agent": agent}
    except AgentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        db.close()


@router.put("/{agent_id}")
def update_agent(request: Request, agent_id: int, data: UpdateAgentRequest):
    """Update a custom agent; built-ins are read-only."""
    user = require_user(request)

    fields = {
        key: value
        for key, value in {
            "name": data.name,
            "description": data.description,
            "system_prompt": data.system_prompt,
            "icon": data.icon,
            "color_index": data.color_index,
            "tools": data.tools,
        }.items()
        if value is not None
    }
    db = create_connection()
    try:
        service = build_ai_agent_service(db)
        agent = service.update_agent(user["id"], agent_id, fields)
        return {"agent": agent}
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except AgentPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except AgentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        db.close()


@router.delete("/{agent_id}")
def delete_agent(request: Request, agent_id: int):
    """Delete a custom agent; conversations fall back to the Assistant."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_agent_service(db)
        service.delete_agent(user["id"], agent_id)
        return {"status": "ok"}
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except AgentPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    finally:
        db.close()


@router.post("/{agent_id}/duplicate")
def duplicate_agent(request: Request, agent_id: int):
    """Copy any agent (built-ins included) into a new custom agent."""
    user = require_user(request)

    db = create_connection()
    try:
        service = build_ai_agent_service(db)
        agent = service.duplicate_agent(user["id"], agent_id)
        return {"agent": agent}
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")
    finally:
        db.close()


