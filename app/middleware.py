"""Application middleware."""
import hashlib
import secrets
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import (
    PUBLIC_PATHS, SESSION_COOKIE,
    CSRF_TOKEN_NAME, CSRF_HEADER_NAME, CSRF_COOKIE_NAME,
    ROOT_PATH, COOKIE_SECURE
)
from .database import create_connection
from .infrastructure.repositories import SessionRepository
from .infrastructure.services.encryption import dek_cache
from .infrastructure.services.session_dek import SessionDEKService
from .infrastructure.services.rate_limiter import RateLimiter
from .infrastructure.services.audit_log import log_session_hijack_detected


def _generate_fingerprint(request: Request) -> str:
    """Generate browser fingerprint from request headers.

    Used to detect session hijacking (cookie copied to different browser).
    """
    # Combine multiple signals for stronger fingerprinting
    user_agent = request.headers.get("user-agent", "")
    accept_lang = request.headers.get("accept-language", "")
    sec_ch_ua = request.headers.get("sec-ch-ua", "")
    sec_ch_ua_platform = request.headers.get("sec-ch-ua-platform", "")

    fingerprint_data = f"{user_agent}:{accept_lang}:{sec_ch_ua}:{sec_ch_ua_platform}"
    return hashlib.sha256(fingerprint_data.encode()).hexdigest()


def strip_root_path(path: str) -> str:
    """Remove root path prefix for path checking."""
    if ROOT_PATH and path.startswith(ROOT_PATH):
        return path[len(ROOT_PATH):] or "/"
    return path


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self';"
        )
        if COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


class BasePathMiddleware(BaseHTTPMiddleware):
    """Middleware to redirect requests from root to base path.
    
    When SYNTH_BASE_URL is set (e.g., 'synth'), redirects requests
    from '/' to '/synth/' to ensure consistent URLs.
    """
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # If base path is set and request is to root, redirect
        if ROOT_PATH and path == "/":
            # Preserve query string
            query = str(request.url.query) if request.url.query else ""
            redirect_url = f"{ROOT_PATH}/"
            if query:
                redirect_url += f"?{query}"
            return RedirectResponse(url=redirect_url, status_code=307)  # 307 preserves method
        
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting for authentication endpoints."""

    _limiter = RateLimiter()

    # Path -> (max_requests, window_seconds)
    _limits = {
        ("POST", "/login"): (5, 15 * 60),
        ("POST", "/api/auth/recover"): (3, 15 * 60),
        ("POST", "/reset-password"): (5, 15 * 60),
    }

    @staticmethod
    def _is_loopback(ip: str) -> bool:
        return ip.startswith("127.") or ip == "::1" or ip.startswith("0:0:0:0:0:0:0:1") or ip == "testclient"

    @classmethod
    def reset(cls):
        """Reset rate limiter state (useful for tests)."""
        cls._limiter = RateLimiter()

    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = strip_root_path(request.url.path)
        limit_key = (method, path)

        if limit_key in self._limits:
            client_ip = request.client.host if request.client else "unknown"
            # Skip rate limiting for loopback (tests and local dev)
            if not self._is_loopback(client_ip):
                max_req, window = self._limits[limit_key]
                key = f"ratelimit:{client_ip}:{method}:{path}"

                if not self._limiter.is_allowed(key, max_req, window):
                    retry_after = window
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many attempts. Please try again later."},
                        headers={"Retry-After": str(retry_after)}
                    )

        return await call_next(request)


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
            # AI endpoints: allow if API key is present (validated in endpoint)
            # Otherwise fall through to normal session auth for user-facing endpoints
            if request.headers.get("X-API-Key"):
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
                    user_id = session["user_id"]
                    
                    # Check fingerprint to prevent session hijacking
                    current_fingerprint = _generate_fingerprint(request)
                    stored_fingerprint = session.get("fingerprint")
                    
                    if stored_fingerprint and stored_fingerprint != current_fingerprint:
                        # Fingerprint mismatch - possible session hijacking
                        # Invalidate DEK cache first, then delete session, and require re-authentication
                        dek_cache.invalidate(user_id)
                        session_repo.delete(session_id)
                        log_session_hijack_detected(
                            session_id=session_id,
                            user_id=user_id,
                            ip=request.client.host if request.client else None,
                            user_agent=request.headers.get("user-agent")
                        )
                        # Continue to "no valid session" handling below
                    else:
                        # Restore DEK from session storage if not in memory cache
                        # This supports server restarts and multiple workers (Issue #18)
                        if dek_cache.get(user_id) is None and session.get("encrypted_dek"):
                            try:
                                dek = SessionDEKService.decrypt_dek(session["encrypted_dek"], session_id)
                                dek_cache.set(user_id, dek)
                            except Exception:
                                # Failed to restore DEK - user may need to re-login
                                pass
                        
                        # Valid session - attach user info to request state
                        user_row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
                        request.state.user = {
                            "id": user_id,
                            "username": session["username"],
                            "display_name": session["display_name"],
                            "is_admin": bool(user_row["is_admin"]) if user_row else False
                        }
                        return await call_next(request)
            finally:
                conn.close()

        # No valid session - redirect to login with next parameter
        if request.method == "GET":
            next_url = request.url.path
            if request.url.query:
                next_url += f"?{request.url.query}"
            # Validate next_url to prevent open redirects
            if not next_url.startswith("/") or next_url.startswith("//") or next_url.startswith("/\\"):
                next_url = f"{ROOT_PATH}/"
            # URL-encode next parameter to preserve query string
            login_url = f"{ROOT_PATH}/login?next={quote(next_url, safe='')}"
            return RedirectResponse(url=login_url, status_code=302)

        # For API calls, return 401
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware to protect against CSRF attacks."""

    # Methods that require CSRF protection
    PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

    # Paths exempt from CSRF (e.g., API endpoints with their own auth)
    EXEMPT_PATHS = {
        "/api/ai/",
        "/api/webauthn/",
        "/api/safes/",
        "/api/auth/recover",
        "/upload",
        "/upload-album",
        "/upload-bulk"
    }

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
            httponly=False,  # JavaScript needs to read this for CSRF protection
            samesite="lax",
            secure=COOKIE_SECURE,  # True in production (HTTPS only)
            max_age=60 * 60 * 24,  # 24 hours
            path="/"
        )
        return response
