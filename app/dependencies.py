"""Shared FastAPI dependencies."""
import hashlib

from fastapi import Request, HTTPException

from .database import create_connection


def get_current_user(request: Request) -> dict | None:
    """Get current user from request state."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    """Require authenticated user, raise 401 if not authenticated."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> dict:
    """Require admin user, raise 403 if not admin."""
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_csrf_token(request: Request) -> str:
    """Get CSRF token from request state."""
    return getattr(request.state, "csrf_token", "")


def require_api_key(request: Request) -> dict:
    """Validate X-API-Key header against ai_api_keys table.

    Returns:
        Dict with api_key info if valid

    Raises:
        HTTPException: 401 if key missing or invalid
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    db = create_connection()
    try:
        cursor = db.execute(
            "SELECT id, name, is_active FROM ai_api_keys WHERE key_hash = ?",
            (key_hash,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="API key inactive")
        return {"id": row["id"], "name": row["name"]}
    finally:
        db.close()
