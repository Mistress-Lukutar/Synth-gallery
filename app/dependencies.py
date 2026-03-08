"""Shared FastAPI dependencies."""
from fastapi import Request, HTTPException


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
