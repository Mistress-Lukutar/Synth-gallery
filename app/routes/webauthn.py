"""WebAuthn routes for hardware key authentication."""
import base64
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import SESSION_COOKIE, SESSION_MAX_AGE, ROOT_PATH, BASE_DIR
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["base_url"] = ROOT_PATH
from ..database import create_connection
from ..infrastructure.repositories import (
    UserRepository, SessionRepository, WebAuthnRepository
)
from ..dependencies import get_current_user
from ..infrastructure.services.webauthn import WebAuthnService, get_rp_id_from_origin, get_origin_from_host
from ..infrastructure.services.encryption import EncryptionService, dek_cache
from ..dependencies import get_csrf_token

router = APIRouter(prefix="/api/webauthn", tags=["webauthn"])
settings_router = APIRouter(tags=["settings"])


def _get_webauthn_params(request: Request) -> tuple[str, str]:
    """Get RP ID and origin from request headers."""
    # Determine scheme from X-Forwarded-Proto or default
    scheme = request.headers.get("x-forwarded-proto", "http")
    if request.url.scheme == "https":
        scheme = "https"

    # Get host from headers
    host = request.headers.get("host", "localhost")

    # Build origin
    origin = get_origin_from_host(host, scheme)

    # Extract RP ID from origin
    rp_id = get_rp_id_from_origin(origin)

    return rp_id, origin


class RegistrationCompleteRequest(BaseModel):
    """Request body for registration completion."""
    credential: dict
    challenge: str  # base64url encoded
    name: str


class AuthenticationCompleteRequest(BaseModel):
    """Request body for authentication completion."""
    credential: dict
    challenge: str  # base64url encoded


class RenameCredentialRequest(BaseModel):
    """Request body for renaming a credential."""
    name: str


def _get_current_user_required(request: Request) -> dict:
    """Get current user or raise 401."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = create_connection()
    try:
        session_repo = SessionRepository(db)
        session = session_repo.get_valid(session_id)
        if not session:
            raise HTTPException(status_code=401, detail="Session expired")

        user_repo = UserRepository(db)
        user = user_repo.get_by_id(session["user_id"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        return dict(user)
    finally:
        db.close()


# === Registration Endpoints ===

@router.get("/register/begin")
def register_begin(request: Request, user: dict = Depends(_get_current_user_required)):
    """Begin WebAuthn registration - generate options for client."""
    # Get RP ID and origin from request
    rp_id, origin = _get_webauthn_params(request)

    db = create_connection()
    try:
        # Get existing credential IDs to exclude
        webauthn_repo = WebAuthnRepository(db)
        existing_cred_ids = webauthn_repo.get_credential_ids_for_user(user["id"])

        options, challenge = WebAuthnService.generate_registration_options_for_user(
            user_id=user["id"],
            username=user["username"],
            display_name=user["display_name"],
            rp_id=rp_id,
            origin=origin,
            existing_credential_ids=existing_cred_ids
        )

        # Return options and challenge (challenge is base64url encoded in options)
        return {
            "options": options,
            "challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
        }
    finally:
        db.close()


@router.post("/register/complete")
def register_complete(
    request: Request,
    body: RegistrationCompleteRequest,
    user: dict = Depends(_get_current_user_required)
):
    """Complete WebAuthn registration - verify response and store credential."""
    # Decode challenge from base64url
    challenge_b64 = body.challenge
    # Add padding if needed
    padding = 4 - len(challenge_b64) % 4
    if padding != 4:
        challenge_b64 += "=" * padding
    challenge = base64.urlsafe_b64decode(challenge_b64)

    # Verify registration
    result = WebAuthnService.verify_registration(
        credential=body.credential,
        challenge=challenge,
        user_id=user["id"]
    )

    if not result:
        raise HTTPException(status_code=400, detail="Registration verification failed")

    credential_id, public_key = result

    # Get user's DEK from cache and encrypt it with a key derived from credential
    # For hardware key auth, we store DEK encrypted with a key derived from the credential
    encrypted_dek = None
    user_dek = dek_cache.get(user["id"])
    if user_dek:
        # Use credential_id as additional entropy for DEK encryption
        # The actual DEK will be decryptable after successful WebAuthn auth
        cred_key = EncryptionService.derive_kek(
            base64.b64encode(credential_id).decode(),
            credential_id[:16] if len(credential_id) >= 16 else credential_id.ljust(16, b'\0')
        )
        encrypted_dek = EncryptionService.encrypt_dek(user_dek, cred_key)

    db = create_connection()
    try:
        # Store credential
        webauthn_repo = WebAuthnRepository(db)
        cred_db_id = webauthn_repo.create(
            user_id=user["id"],
            credential_id=credential_id,
            public_key=public_key,
            name=body.name.strip() or "Security Key",
            encrypted_dek=encrypted_dek
        )

        return {"success": True, "credential_id": cred_db_id}
    finally:
        db.close()


# === Authentication Endpoints ===

@router.get("/authenticate/begin")
def authenticate_begin(request: Request, username: str = None):
    """Begin WebAuthn authentication - generate options for client.

    If username is provided, generates options for that user's credentials.
    If not, generates discoverable credential options (passwordless).
    """
    # Get RP ID and origin from request
    rp_id, origin = _get_webauthn_params(request)

    db = create_connection()
    try:
        if username:
            # Get user
            user_repo = UserRepository(db)
            user = user_repo.get_by_username(username)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            webauthn_repo = WebAuthnRepository(db)
            credential_ids = webauthn_repo.get_credential_ids_for_user(user["id"])
            if not credential_ids:
                raise HTTPException(status_code=404, detail="No hardware keys registered")

            options, challenge = WebAuthnService.generate_authentication_options_for_user(
                user_id=user["id"],
                credential_ids=credential_ids,
                rp_id=rp_id,
                origin=origin
            )
        else:
            # Discoverable credentials (passwordless)
            options, challenge = WebAuthnService.generate_authentication_options_discoverable(
                rp_id=rp_id,
                origin=origin
            )

        return {
            "options": options,
            "challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
        }
    finally:
        db.close()


@router.post("/authenticate/complete")
def authenticate_complete(request: Request, body: AuthenticationCompleteRequest):
    """Complete WebAuthn authentication - verify response and create session."""
    # Decode challenge
    challenge_b64 = body.challenge
    padding = 4 - len(challenge_b64) % 4
    if padding != 4:
        challenge_b64 += "=" * padding
    challenge = base64.urlsafe_b64decode(challenge_b64)

    # Get credential_id from response
    raw_id_b64 = body.credential.get("rawId", body.credential.get("id", ""))
    padding = 4 - len(raw_id_b64) % 4
    if padding != 4:
        raw_id_b64 += "=" * padding
    credential_id = base64.urlsafe_b64decode(raw_id_b64)

    db = create_connection()
    try:
        # Find credential in database
        webauthn_repo = WebAuthnRepository(db)
        cred = webauthn_repo.get_by_credential_id(credential_id)
        if not cred:
            raise HTTPException(status_code=401, detail="Credential not found")

        # Verify authentication
        new_sign_count = WebAuthnService.verify_authentication(
            credential=body.credential,
            challenge=challenge,
            credential_public_key=cred["public_key"],
            credential_current_sign_count=cred["sign_count"]
        )

        if new_sign_count is None:
            raise HTTPException(status_code=401, detail="Authentication failed")

        # Update sign count
        webauthn_repo.update_sign_count(credential_id, new_sign_count)

        # Create session
        session_repo = SessionRepository(db)
        session_id = session_repo.create(cred["user_id"])

        # Decrypt DEK from credential if available
        if cred.get("encrypted_dek"):
            try:
                cred_key = EncryptionService.derive_kek(
                    base64.b64encode(credential_id).decode(),
                    credential_id[:16] if len(credential_id) >= 16 else credential_id.ljust(16, b'\0')
                )
                dek = EncryptionService.decrypt_dek(cred["encrypted_dek"], cred_key)
                dek_cache.set(cred["user_id"], dek, ttl_seconds=SESSION_MAX_AGE)
            except Exception:
                # DEK decryption failed - user may need to re-authenticate with password
                pass

        # Set session cookie
        response = JSONResponse({"success": True, "username": cred["username"]})
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=SESSION_MAX_AGE
        )
        return response
    finally:
        db.close()


# === Credential Management Endpoints ===

@router.get("/credentials")
def list_credentials(user: dict = Depends(_get_current_user_required)):
    """List all registered credentials for current user."""
    db = create_connection()
    try:
        webauthn_repo = WebAuthnRepository(db)
        credentials = webauthn_repo.get_for_user(user["id"])

        # Don't expose sensitive data
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "created_at": c["created_at"],
                "has_dek": c["encrypted_dek"] is not None
            }
            for c in credentials
        ]
    finally:
        db.close()


@router.delete("/credentials/{credential_id}")
def delete_credential(
    credential_id: int,
    user: dict = Depends(_get_current_user_required)
):
    """Delete a registered credential."""
    db = create_connection()
    try:
        webauthn_repo = WebAuthnRepository(db)
        success = webauthn_repo.delete(credential_id, user["id"])
        if not success:
            raise HTTPException(status_code=404, detail="Credential not found")

        return {"success": True}
    finally:
        db.close()


@router.patch("/credentials/{credential_id}")
def rename_credential(
    credential_id: int,
    body: RenameCredentialRequest,
    user: dict = Depends(_get_current_user_required)
):
    """Rename a registered credential."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    db = create_connection()
    try:
        webauthn_repo = WebAuthnRepository(db)
        success = webauthn_repo.rename(credential_id, user["id"], body.name.strip())
        if not success:
            raise HTTPException(status_code=404, detail="Credential not found")

        return {"success": True}
    finally:
        db.close()


# === Check if user has hardware keys ===

@router.get("/check/{username}")
def check_user_has_keys(username: str):
    """Check if a user has registered hardware keys (for login page)."""
    db = create_connection()
    try:
        user_repo = UserRepository(db)
        user = user_repo.get_by_username(username)
        if not user:
            return {"has_keys": False}

        webauthn_repo = WebAuthnRepository(db)
        credential_ids = webauthn_repo.get_credential_ids_for_user(user["id"])
        return {"has_keys": len(credential_ids) > 0}
    finally:
        db.close()


# === Settings Page ===

@settings_router.get("/settings")
def settings_page(request: Request):
    """User settings page."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

    db = create_connection()
    try:
        session_repo = SessionRepository(db)
        session = session_repo.get_valid(session_id)
        if not session:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

        user_repo = UserRepository(db)
        user = user_repo.get_by_id(session["user_id"])
        if not user:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"{ROOT_PATH}/login", status_code=302)

        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "user": dict(user),
                "csrf_token": get_csrf_token(request),
                "base_url": ROOT_PATH
            }
        )
    finally:
        db.close()
