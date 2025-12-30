"""Authentication routes."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import BASE_DIR, SESSION_COOKIE, SESSION_MAX_AGE
from ..database import authenticate_user, create_session, delete_session, get_session
from ..dependencies import get_csrf_token

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
        delete_session(session_id)

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
