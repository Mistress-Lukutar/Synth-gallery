"""Authentication routes."""
import hashlib

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..application.services import AuthService
from ..config import SESSION_COOKIE, SESSION_MAX_AGE, ROOT_PATH, COOKIE_SECURE, BASE_DIR
from ..database import create_connection
from ..dependencies import get_csrf_token
from ..infrastructure.repositories import UserRepository, SessionRepository
from ..infrastructure.services.encryption import dek_cache


def _generate_fingerprint(request: Request) -> str:
    """Generate browser fingerprint from request headers."""
    user_agent = request.headers.get("user-agent", "")
    accept_lang = request.headers.get("accept-language", "")
    fingerprint_data = f"{user_agent}:{accept_lang}"
    return hashlib.sha256(fingerprint_data.encode()).hexdigest()[:32]

router = APIRouter()

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH


def get_auth_service() -> AuthService:
    """Create AuthService with repositories."""
    db = create_connection()
    return AuthService(
        user_repository=UserRepository(db),
        session_repository=SessionRepository(db)
    )


@router.get("/login")
def login_page(request: Request, error: str = None):
    """Show login page."""
    # If already logged in, check if we can auto-redirect
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        service = get_auth_service()
        session = service.get_session(session_id)
        if session:
            user_id = session["user_id"]
            enc_keys = service.get_encryption_keys(user_id)

            # If user has encryption but DEK not in cache, need password re-entry
            # This happens after server restart or when DEK cache expires
            if enc_keys and not service.is_dek_cached(user_id):
                # Show login page with info message
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    {
                        "error": "Session restored. Please enter password to decrypt your files.",
                        "username": session["username"],
                        "csrf_token": get_csrf_token(request),
                        "base_url": ROOT_PATH
                    }
                )

            # DEK is in cache or user has no encryption, safe to redirect
            return RedirectResponse(url=f"{ROOT_PATH}/", status_code=302)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "username": "",
            "csrf_token": get_csrf_token(request),
            "base_url": ROOT_PATH
        }
    )


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Process login form."""
    service = get_auth_service()
    user = service.authenticate(username, password)

    if not user:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Invalid username or password",
                "username": username,
                "csrf_token": get_csrf_token(request),
                "base_url": ROOT_PATH
            },
            status_code=401
        )

    # Create session with fingerprint for hijacking protection
    fingerprint = _generate_fingerprint(request)
    session_id = service.create_session(user["id"], fingerprint=fingerprint)

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

    # Redirect to gallery with session cookie
    response = RedirectResponse(url=f"{ROOT_PATH}/", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,  # Session cookie not accessible via JavaScript
        samesite="lax",
        secure=COOKIE_SECURE,  # True in production (HTTPS only)
        max_age=SESSION_MAX_AGE
    )
    return response


@router.get("/logout")
def logout(request: Request):
    """Logout user."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        service = get_auth_service()
        # Get user ID before deleting session to clear DEK cache
        session = service.get_session(session_id)
        if session:
            dek_cache.invalidate(session["user_id"])
        service.delete_session(session_id)

    response = RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
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
    service = get_auth_service()
    
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
    
    # Validate token
    service = get_auth_service()
    user_id = service.validate_reset_token(token)
    
    if not user_id:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {
                "error": "Invalid or expired reset link. Please use recovery key again.",
                "valid_token": False,
                "reset_token": None,
                "csrf_token": get_csrf_token(request),
                "base_url": ROOT_PATH
            }
        )
    
    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {
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
    
    if len(new_password) < 4:
        return RedirectResponse(
            url=f"{ROOT_PATH}/reset-password?token={reset_token}&error=Password+must+be+at+least+4+characters",
            status_code=302
        )
    
    service = get_auth_service()
    
    try:
        fingerprint = _generate_fingerprint(request)
        user, session_id = service.complete_password_reset(
            reset_token,
            new_password,
            fingerprint=fingerprint
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
