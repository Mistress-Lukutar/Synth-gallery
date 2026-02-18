"""Authentication routes."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from ..config import SESSION_COOKIE, SESSION_MAX_AGE, ROOT_PATH, BASE_DIR
from fastapi.templating import Jinja2Templates

from ..database import create_connection
from ..infrastructure.repositories import UserRepository, SessionRepository
from ..application.services import AuthService
from ..dependencies import get_csrf_token
from ..services.encryption import EncryptionService, dek_cache

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

    # Create session
    session_id = service.create_session(user["id"])

    # Handle encryption key
    enc_keys = service.get_encryption_keys(user["id"])

    if enc_keys:
        # User has encryption set up - decrypt DEK and cache it
        service.decrypt_and_cache_dek(user["id"], password, ttl_seconds=SESSION_MAX_AGE)
    else:
        # New user or encryption not set up yet - generate DEK
        service.setup_encryption(user["id"], password)

    # Redirect to gallery with session cookie
    response = RedirectResponse(url=f"{ROOT_PATH}/", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
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
