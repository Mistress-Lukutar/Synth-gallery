"""Application middleware."""
import secrets
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import (
    PUBLIC_PATHS, SESSION_COOKIE,
    CSRF_TOKEN_NAME, CSRF_HEADER_NAME, CSRF_COOKIE_NAME,
    ROOT_PATH
)
from .database import create_connection
from .infrastructure.repositories import SessionRepository


def strip_root_path(path: str) -> str:
    """Remove root path prefix for path checking."""
    if ROOT_PATH and path.startswith(ROOT_PATH):
        return path[len(ROOT_PATH):] or "/"
    return path


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication on all routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Strip root path for checking against PUBLIC_PATHS
        check_path = strip_root_path(path)

        # Allow public paths
        if check_path in PUBLIC_PATHS or check_path.startswith("/static/"):
            return await call_next(request)

        # Allow API paths with API key (for AI service)
        if check_path.startswith("/api/ai/"):
            # AI endpoints have their own auth via API key
            return await call_next(request)

        # Allow WebAuthn authentication paths (for passwordless login)
        if check_path.startswith("/api/webauthn/authenticate/") or check_path.startswith("/api/webauthn/check/"):
            return await call_next(request)

        # Check session cookie - use separate connection to avoid conflicts
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            conn = create_connection()
            try:
                session_repo = SessionRepository(conn)
                session = session_repo.get_valid(session_id)
                if session:
                    # Valid session - attach user info to request state
                    request.state.user = {
                        "id": session["user_id"],
                        "username": session["username"],
                        "display_name": session["display_name"]
                    }
                    return await call_next(request)
            finally:
                conn.close()

        # No valid session - redirect to login
        if request.method == "GET":
            return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

        # For API calls, return 401
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware to protect against CSRF attacks."""

    # Methods that require CSRF protection
    PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

    # Paths exempt from CSRF (e.g., API endpoints with their own auth)
    EXEMPT_PATHS = {"/api/ai/", "/api/webauthn/", "/api/safes/", "/api/envelope/", "/upload", "/upload-album", "/upload-bulk"}

    async def dispatch(self, request: Request, call_next):
        # Generate CSRF token if not present
        csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)

        # Store token in request state for templates
        request.state.csrf_token = csrf_token

        # Strip root path for checking
        check_path = strip_root_path(request.url.path)

        # Check CSRF for protected methods
        if request.method in self.PROTECTED_METHODS:
            # Skip CSRF check for exempt paths
            if any(check_path.startswith(p) for p in self.EXEMPT_PATHS):
                response = await call_next(request)
                return self._set_csrf_cookie(response, csrf_token)

            # Skip CSRF check for public paths (like login)
            if check_path in PUBLIC_PATHS:
                response = await call_next(request)
                return self._set_csrf_cookie(response, csrf_token)

            # Get token from header or form
            request_token = request.headers.get(CSRF_HEADER_NAME)

            # For form submissions, we'll need to check form data
            # But since FastAPI processes body later, we check header first
            if not request_token:
                # Try to get from query params (for simple forms)
                request_token = request.query_params.get(CSRF_TOKEN_NAME)

            # Validate token
            stored_token = request.cookies.get(CSRF_COOKIE_NAME)
            if not stored_token or not request_token or stored_token != request_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid"}
                )

        response = await call_next(request)
        return self._set_csrf_cookie(response, csrf_token)

    def _set_csrf_cookie(self, response, token: str):
        """Set CSRF cookie on response."""
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            httponly=False,  # JavaScript needs to read this
            samesite="lax",
            secure=False,  # Set to True in production with HTTPS
            max_age=60 * 60 * 24  # 24 hours
        )
        return response
