"""Authentication routes."""
import hashlib
from contextlib import contextmanager

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..application.services import AuthService
from ..config import SESSION_COOKIE, SESSION_MAX_AGE, ROOT_PATH, COOKIE_SECURE, BASE_DIR, EXTERNAL_HOST
from ..database import create_connection
from ..dependencies import get_csrf_token
from ..infrastructure.repositories import UserRepository, SessionRepository
from ..infrastructure.services.encryption import dek_cache
from ..infrastructure.services.audit_log import (
    log_failed_login,
    log_successful_login,
    log_logout,
    log_password_reset,
)


def _generate_fingerprint(request: Request) -> str:
    """Generate browser fingerprint from request headers."""
    user_agent = request.headers.get("user-agent", "")
    accept_lang = request.headers.get("accept-language", "")
    sec_ch_ua = request.headers.get("sec-ch-ua", "")
    sec_ch_ua_platform = request.headers.get("sec-ch-ua-platform", "")
    fingerprint_data = f"{user_agent}:{accept_lang}:{sec_ch_ua}:{sec_ch_ua_platform}"
    return hashlib.sha256(fingerprint_data.encode()).hexdigest()

router = APIRouter()

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH
templates.env.globals["external_host"] = EXTERNAL_HOST


@contextmanager
def get_auth_service():
    """Create AuthService with repositories and ensure connection is closed."""
    db = create_connection()
    try:
        yield AuthService(
            user_repository=UserRepository(db),
            session_repository=SessionRepository(db)
        )
    finally:
        db.close()


def _is_safe_redirect(url: str) -> bool:
    """Validate redirect URL to prevent open redirects."""
    return (
        url.startswith("/")
        and not url.startswith("//")
        and not url.startswith("/\\")
    )


@router.get("/login")
def login_page(request: Request, error: str = None, next: str = None):
    """Show login page."""
    # If already logged in, check if we can auto-redirect
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        with get_auth_service() as service:
            session = service.get_session(session_id)
            if session:
                user_id = session["user_id"]
                enc_keys = service.get_encryption_keys(user_id)

                # If user has encryption but DEK not in cache, need password re-entry
                # This happens after server restart or when DEK cache expires
                if enc_keys and not service.is_dek_cached(user_id):
                    # Show login page with info message
                    return templates.TemplateResponse(
                        "login.html",
                        {
                            "request": request,
                            "error": "Session restored. Please enter password to decrypt your files.",
                            "username": session["username"],
                            "csrf_token": get_csrf_token(request),
                            "base_url": ROOT_PATH,
                            "next": next or ""
                        }
                    )

                # DEK is in cache or user has no encryption, safe to redirect to next or home
                redirect_url = next if next and _is_safe_redirect(next) else f"{ROOT_PATH}/"
                return RedirectResponse(url=redirect_url, status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "username": "",
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH,
            "next": next or ""
        }
    )


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("")):
    """Process login form."""
    with get_auth_service() as service:
        user = service.authenticate(username, password)

    if not user:
        log_failed_login(
            username=username,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password",
                "username": username,
                "csrf_token": get_csrf_token(request),
                "base_url": ROOT_PATH,
                "next": next
            },
            status_code=401
        )

    log_successful_login(
        user_id=user["id"],
        username=user["username"],
        ip=request.client.host if request.client else None
    )

    # Create session with fingerprint for hijacking protection
    fingerprint = _generate_fingerprint(request)
    with get_auth_service() as service:
        session_id = service.create_session(user["id"], fingerprint=fingerprint)

    with get_auth_service() as service:
        # Handle encryption key
        enc_keys = service.get_encryption_keys(user["id"])

        if enc_keys:
            # User has encryption set up - decrypt DEK and cache it
            # Pass session_id to store DEK in session for persistence (Issue #18)
            service.decrypt_and_cache_dek(user["id"], password, session_id=session_id, ttl_seconds=SESSION_MAX_AGE)
        else:
            # New user or encryption not set up yet - generate DEK
            dek, salt = service.setup_encryption(user["id"], password)
            # Store DEK in session for persistence (Issue #18)
            service.store_dek_in_session(session_id, dek)

    # Redirect to next URL or gallery with session cookie
    redirect_url = next if next and _is_safe_redirect(next) else f"{ROOT_PATH}/"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,  # Session cookie not accessible via JavaScript
        samesite="lax",
        secure=COOKIE_SECURE,  # True in production (HTTPS only)
        max_age=SESSION_MAX_AGE,
        path="/"  # Ensure cookie is valid for entire site
    )
    return response


@router.get("/logout")
def logout(request: Request):
    """Logout user."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        with get_auth_service() as service:
            # Get user ID before deleting session to clear DEK cache
            session = service.get_session(session_id)
            if session:
                dek_cache.invalidate(session["user_id"])
            service.delete_session(session_id)

    response = RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ============================================================================
# Recovery Key Login
# ============================================================================

class RecoveryKeyLoginRequest(BaseModel):
    username: str
    recovery_key: str


class PasswordResetRequest(BaseModel):
    reset_token: str
    new_password: str


@router.post("/api/auth/recover")
def recover_with_key(request: Request, data: RecoveryKeyLoginRequest):
    """Authenticate with recovery key and get reset token."""
    with get_auth_service() as service:
        user, reset_token = service.authenticate_with_recovery_key(
            data.username,
            data.recovery_key
        )
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or recovery key")
    
    return {
        "status": "ok",
        "message": "Recovery successful. Please set a new password.",
        "reset_token": reset_token,
        "username": user["username"]
    }


@router.get("/reset-password")
def reset_password_page(
    request: Request,
    token: str = None,
    error: str = None
):
    """Show password reset page after recovery key login."""
    if not token:
        return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)
    
    with get_auth_service() as service:
        user_id = service.validate_reset_token(token)
    
    if not user_id:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "error": "Invalid or expired reset link. Please use recovery key again.",
                "valid_token": False,
                "reset_token": None,
                "csrf_token": get_csrf_token(request),
                "base_url": ROOT_PATH
            }
        )
    
    return templates.TemplateResponse(
        "reset_password.html",
        {
            "request": request,
            "error": error,
            "valid_token": True,
            "reset_token": token,
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH
        }
    )


@router.post("/reset-password")
def reset_password(
    request: Request,
    reset_token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...)
):
    """Process password reset after recovery key login."""
    # Verify CSRF
    expected_token = getattr(request.state, "csrf_token", "")
    if not expected_token or csrf_token != expected_token:
        return RedirectResponse(
            url=f"{ROOT_PATH}/reset-password?token={reset_token}&error=Invalid+CSRF+token",
            status_code=302
        )
    
    if new_password != confirm_password:
        return RedirectResponse(
            url=f"{ROOT_PATH}/reset-password?token={reset_token}&error=Passwords+do+not+match",
            status_code=302
        )
    
    if len(new_password) < 12:
        return RedirectResponse(
            url=f"{ROOT_PATH}/reset-password?token={reset_token}&error=Password+must+be+at+least+12+characters",
            status_code=302
        )

    with get_auth_service() as service:
        try:
            fingerprint = _generate_fingerprint(request)
            user, session_id = service.complete_password_reset(
                reset_token,
                new_password,
                fingerprint=fingerprint
            )

            log_password_reset(
                user_id=user["id"],
                ip=request.client.host if request.client else None
            )

            # Redirect to gallery with session cookie
            response = RedirectResponse(url=f"{ROOT_PATH}/", status_code=302)
            response.set_cookie(
                key=SESSION_COOKIE,
                value=session_id,
                httponly=True,
                samesite="lax",
                secure=COOKIE_SECURE,
                max_age=SESSION_MAX_AGE
            )
            return response

        except HTTPException as e:
            return RedirectResponse(
                url=f"{ROOT_PATH}/reset-password?token={reset_token}&error={e.detail}",
                status_code=302
            )
