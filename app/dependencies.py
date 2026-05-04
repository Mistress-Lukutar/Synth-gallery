"""Shared FastAPI dependencies."""
import hashlib
import time
from typing import Dict, List

import bcrypt
from fastapi import Request, HTTPException

from .database import create_connection
from .infrastructure.repositories import AiApiKeyRepository
from .infrastructure.services.audit_log import log_api_key_failure


# In-memory rate limiter for API keys (sufficient for single-instance deployments)
# Format: {api_key_hash: [timestamps]}
_api_key_rate_limiter: Dict[str, List[float]] = {}


def _check_rate_limit(key_hash: str, max_requests: int = 60, window_seconds: int = 60) -> bool:
    """Check if API key is within rate limit.

    Args:
        key_hash: Hash of the API key
        max_requests: Maximum requests allowed in the window
        window_seconds: Time window in seconds

    Returns:
        True if within limit, False if exceeded
    """
    now = time.time()
    window_start = now - window_seconds

    timestamps = _api_key_rate_limiter.get(key_hash, [])
    # Remove old timestamps
    timestamps = [t for t in timestamps if t > window_start]

    if len(timestamps) >= max_requests:
        _api_key_rate_limiter[key_hash] = timestamps
        return False

    timestamps.append(now)
    _api_key_rate_limiter[key_hash] = timestamps
    return True


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

    Uses bcrypt for key hash verification. Returns API key metadata including
    the associated user_id for endpoint authorization.

    Returns:
        Dict with api_key info if valid: {"id", "name", "user_id"}

    Raises:
        HTTPException: 401 if key missing or invalid, 429 if rate limited
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    # Temporary rate limiting (to be replaced by proper middleware in future)
    sha_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if not _check_rate_limit(sha_hash, max_requests=60, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"}
        )

    db = create_connection()
    try:
        repo = AiApiKeyRepository(db)
        # First try bcrypt hash lookup
        cursor = db.execute(
            "SELECT id, name, key_hash, is_active, user_id, expires_at FROM ai_api_keys"
        )
        for row in cursor.fetchall():
            key_hash_db = row["key_hash"]
            # Support both bcrypt and legacy SHA-256 hashes during migration
            if key_hash_db.startswith(("$2b$", "$2a$")):
                if bcrypt.checkpw(api_key.encode(), key_hash_db.encode()):
                    matched = row
                    break
            else:
                if key_hash_db == sha_hash:
                    matched = row
                    break
        else:
            log_api_key_failure(
                ip=request.client.host if request.client else None,
                reason="invalid_key"
            )
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not matched["is_active"]:
            log_api_key_failure(
                ip=request.client.host if request.client else None,
                reason="inactive_key"
            )
            raise HTTPException(status_code=401, detail="API key inactive")

        # Check expiration
        if matched["expires_at"]:
            from datetime import datetime
            if isinstance(matched["expires_at"], str):
                expires = datetime.fromisoformat(matched["expires_at"])
            else:
                expires = matched["expires_at"]
            if datetime.now() > expires:
                log_api_key_failure(
                    ip=request.client.host if request.client else None,
                    reason="expired_key"
                )
                raise HTTPException(status_code=401, detail="API key expired")

        # Update last_used_at
        repo.update_last_used(matched["id"])

        return {
            "id": matched["id"],
            "name": matched["name"],
            "user_id": matched["user_id"]
        }
    finally:
        db.close()
