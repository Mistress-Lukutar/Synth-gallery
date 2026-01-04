"""Authentication routes."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import BASE_DIR, SESSION_COOKIE, SESSION_MAX_AGE
from ..database import (
    authenticate_user, create_session, delete_session, get_session,
    get_user_encryption_keys, set_user_encryption_keys
)
from ..dependencies import get_csrf_token
from ..services.encryption import EncryptionService, dek_cache

router = APIRouter()
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@router.get("/login")
def login_page(request: Request, error: str = None):
    """Show login page."""
    # If already logged in, redirect to gallery
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id and get_session(session_id):
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "username": "",
            "csrf_token": get_csrf_token(request)
        }
    )


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Process login form."""
    user = authenticate_user(username, password)

    if not user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password",
                "username": username,
                "csrf_token": get_csrf_token(request)
            },
            status_code=401
        )

    # Create session
    session_id = create_session(user["id"])

    # Handle encryption key
    enc_keys = get_user_encryption_keys(user["id"])

    if enc_keys:
        # User has encryption set up - decrypt DEK and cache it
        try:
            kek = EncryptionService.derive_kek(password, enc_keys["dek_salt"])
            dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], kek)
            dek_cache.set(user["id"], dek, ttl_seconds=SESSION_MAX_AGE)
        except Exception:
            # DEK decryption failed - possibly password changed via CLI
            # User will need to re-encrypt their files
            pass
    else:
        # New user or encryption not set up yet - generate DEK
        dek = EncryptionService.generate_dek()
        salt = EncryptionService.generate_salt()
        kek = EncryptionService.derive_kek(password, salt)
        encrypted_dek = EncryptionService.encrypt_dek(dek, kek)
        set_user_encryption_keys(user["id"], encrypted_dek, salt)
        dek_cache.set(user["id"], dek, ttl_seconds=SESSION_MAX_AGE)

    # Redirect to gallery with session cookie
    response = RedirectResponse(url="/", status_code=302)
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
        # Get user ID before deleting session to clear DEK cache
        session = get_session(session_id)
        if session:
            dek_cache.invalidate(session["user_id"])
        delete_session(session_id)

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
