"""Shared FastAPI dependencies."""
from fastapi import Request, HTTPException, Header
from typing import Optional

from .config import AI_API_KEY


def get_current_user(request: Request) -> dict | None:
    """Get current user from request state."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    """Require authenticated user, raise 401 if not authenticated."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_csrf_token(request: Request) -> str:
    """Get CSRF token from request state."""
    return getattr(request.state, "csrf_token", "")


async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> bool:
    """Verify API key for AI service endpoints.

    Returns True if API key is valid, raises HTTPException otherwise.
    """
    if not AI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="AI API key not configured. Set SYNTH_AI_API_KEY environment variable."
        )

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header."
        )

    if x_api_key != AI_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return True
