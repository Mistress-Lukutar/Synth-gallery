"""Safe (encrypted vault) management routes."""
import base64
import secrets
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..database import (
    get_db, get_safe, get_user_safes, create_safe, update_safe, delete_safe,
    create_safe_session, get_safe_session, delete_safe_session, 
    is_safe_unlocked_for_user, get_user_unlocked_safes, cleanup_expired_safe_sessions,
    get_webauthn_credential_by_id, get_webauthn_credentials,
    is_folder_in_safe, get_folder_safe_id
)
from ..dependencies import require_user
from ..services.encryption import EncryptionService

router = APIRouter(prefix="/api/safes", tags=["safes"])


# =============================================================================
# Request/Response Models
# =============================================================================

class SafeCreate(BaseModel):
    name: str
    unlock_type: str  # 'password' or 'webauthn'
    password: Optional[str] = None  # Required if unlock_type='password'
    credential_id: Optional[str] = None  # Required if unlock_type='webauthn'
    encrypted_dek: str  # base64-encoded DEK encrypted with safe key
    salt: Optional[str] = None  # base64-encoded salt (for password)


class SafeUnlock(BaseModel):
    safe_id: str
    password: Optional[str] = None
    credential_id: Optional[str] = None  # For WebAuthn unlock


class SafeUnlockResponse(BaseModel):
    session_id: str
    expires_at: str


class SafeRename(BaseModel):
    name: str


# =============================================================================
# Safe Management Endpoints
# =============================================================================

@router.get("")
def list_safes(request: Request):
    """Get all safes for current user with unlock status."""
    user = require_user(request)
    
    safes = get_user_safes(user["id"])
    unlocked = get_user_unlocked_safes(user["id"])
    
    return {
        "safes": [
            {
                "id": s["id"],
                "name": s["name"],
                "created_at": s["created_at"],
                "unlock_type": s["unlock_type"],
                "folder_count": s["folder_count"],
                "photo_count": s["photo_count"],
                "is_unlocked": s["id"] in unlocked
            }
            for s in safes
        ]
    }


@router.post("")
def create_new_safe(request: Request, data: SafeCreate):
    """Create a new safe."""
    user = require_user(request)
    
    # Validate unlock type
    if data.unlock_type not in ('password', 'webauthn'):
        raise HTTPException(status_code=400, detail="unlock_type must be 'password' or 'webauthn'")
    
    # Validate based on unlock type
    if data.unlock_type == 'password':
        if not data.password or len(data.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        if not data.salt:
            raise HTTPException(status_code=400, detail="Salt required for password-based safe")
    elif data.unlock_type == 'webauthn':
        if not data.credential_id:
            raise HTTPException(status_code=400, detail="credential_id required for WebAuthn safe")
        # Verify credential belongs to user
        cred_id_bytes = base64.urlsafe_b64decode(data.credential_id + '=' * (4 - len(data.credential_id) % 4))
        cred = get_webauthn_credential_by_id(cred_id_bytes)
        if not cred or cred["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Invalid credential")
    
    # Decode encrypted DEK (URL-safe base64 from client)
    try:
        encrypted_dek = base64.urlsafe_b64decode(data.encrypted_dek + '=' * (4 - len(data.encrypted_dek) % 4))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid encrypted_dek format")
    
    # Decode salt if provided (URL-safe base64 from client)
    salt = None
    if data.salt:
        try:
            salt = base64.urlsafe_b64decode(data.salt + '=' * (4 - len(data.salt) % 4))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid salt format")
    
    # Decode credential_id if provided (URL-safe base64 from client)
    credential_id = None
    if data.credential_id:
        try:
            credential_id = base64.urlsafe_b64decode(data.credential_id + '=' * (4 - len(data.credential_id) % 4))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid credential_id format")
    
    # Create safe
    safe_id = create_safe(
        name=data.name,
        user_id=user["id"],
        encrypted_dek=encrypted_dek,
        unlock_type=data.unlock_type,
        credential_id=credential_id,
        salt=salt
    )
    
    return {
        "status": "ok",
        "safe_id": safe_id,
        "message": "Safe created successfully"
    }


@router.get("/{safe_id}")
def get_safe_details(safe_id: str, request: Request):
    """Get safe details."""
    user = require_user(request)
    
    safe = get_safe(safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    is_unlocked = is_safe_unlocked_for_user(safe_id, user["id"])
    
    return {
        "id": safe["id"],
        "name": safe["name"],
        "created_at": safe["created_at"],
        "unlock_type": safe["unlock_type"],
        "is_unlocked": is_unlocked,
        "has_recovery": safe["recovery_encrypted_dek"] is not None
    }


@router.put("/{safe_id}")
def rename_safe(safe_id: str, data: SafeRename, request: Request):
    """Rename a safe."""
    user = require_user(request)
    
    safe = get_safe(safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_safe(safe_id, data.name)
    
    return {"status": "ok", "name": data.name}


@router.delete("/{safe_id}")
def delete_safe_route(safe_id: str, request: Request):
    """Delete a safe and all its contents."""
    user = require_user(request)
    
    safe = get_safe(safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    delete_safe(safe_id)
    
    return {"status": "ok", "message": "Safe deleted"}


# =============================================================================
# Safe Unlock/Lock Endpoints
# =============================================================================

@router.post("/unlock")
def unlock_safe(request: Request, data: SafeUnlock):
    """Unlock a safe with password or WebAuthn.
    
    This endpoint initiates the unlock process. For WebAuthn, it returns
    challenge data that the client must complete.
    """
    user = require_user(request)
    
    safe = get_safe(data.safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if safe["unlock_type"] == 'password':
        # Return challenge data for client-side decryption
        # Client will use encrypted_dek and salt to verify password locally
        print(f"[DEBUG] Safe {data.safe_id}: unlock_type={safe['unlock_type']}, has_salt={safe['salt'] is not None}, has_encrypted_dek={safe['encrypted_dek'] is not None}")
        
        if not safe["salt"]:
            raise HTTPException(status_code=500, detail="Safe is missing salt - may have been created with old code")
        if not safe["encrypted_dek"]:
            raise HTTPException(status_code=500, detail="Safe is missing encrypted_dek")
        
        return {
            "status": "challenge",
            "type": "password",
            "encrypted_dek": base64.urlsafe_b64encode(safe["encrypted_dek"]).decode().rstrip('='),
            "salt": base64.urlsafe_b64encode(safe["salt"]).decode().rstrip('=')
        }
    
    elif safe["unlock_type"] == 'webauthn':
        # Return WebAuthn challenge
        from ..services.webauthn import WebAuthnService, get_rp_id_from_origin, get_origin_from_host
        
        scheme = request.headers.get("x-forwarded-proto", "https" if request.url.scheme == "https" else "http")
        host = request.headers.get("host", "localhost")
        origin = get_origin_from_host(host, scheme)
        rp_id = get_rp_id_from_origin(origin)
        
        # Get the specific credential for this safe
        credential_ids = [safe["credential_id"]] if safe["credential_id"] else []
        
        options, challenge = WebAuthnService.generate_authentication_options_for_user(
            user_id=user["id"],
            credential_ids=credential_ids,
            rp_id=rp_id,
            origin=origin
        )
        
        return {
            "status": "challenge",
            "type": "webauthn",
            "options": options,
            "challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
        }
    
    else:
        raise HTTPException(status_code=400, detail="Invalid unlock type")


class SafeUnlockComplete(BaseModel):
    safe_id: str
    # For password unlock: encrypted DEK from client (to create session)
    # For WebAuthn: authentication response
    challenge: Optional[str] = None
    credential: Optional[dict] = None
    # Session-encrypted DEK from client
    session_encrypted_dek: str  # DEK encrypted with session key


@router.post("/unlock/complete")
def complete_safe_unlock(request: Request, data: SafeUnlockComplete):
    """Complete safe unlock and create session."""
    user = require_user(request)
    
    safe = get_safe(data.safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if safe["unlock_type"] == 'webauthn':
        # Verify WebAuthn response
        from ..services.webauthn import WebAuthnService
        from ..database import update_webauthn_sign_count
        
        if not data.credential or not data.challenge:
            raise HTTPException(status_code=400, detail="WebAuthn credential and challenge required")
        
        # Decode challenge
        challenge_b64 = data.challenge
        padding = 4 - len(challenge_b64) % 4
        if padding != 4:
            challenge_b64 += "=" * padding
        challenge = base64.urlsafe_b64decode(challenge_b64)
        
        # Get credential
        raw_id_b64 = data.credential.get("rawId", data.credential.get("id", ""))
        padding = 4 - len(raw_id_b64) % 4
        if padding != 4:
            raw_id_b64 += "=" * padding
        credential_id = base64.urlsafe_b64decode(raw_id_b64)
        
        cred = get_webauthn_credential_by_id(credential_id)
        if not cred:
            raise HTTPException(status_code=401, detail="Credential not found")
        
        # Verify authentication
        new_sign_count = WebAuthnService.verify_authentication(
            credential=data.credential,
            challenge=challenge,
            credential_public_key=cred["public_key"],
            credential_current_sign_count=cred["sign_count"]
        )
        
        if new_sign_count is None:
            raise HTTPException(status_code=401, detail="WebAuthn authentication failed")
        
        # Update sign count
        update_webauthn_sign_count(credential_id, new_sign_count)
    
    # Decode session-encrypted DEK (URL-safe base64 from client)
    try:
        session_encrypted_dek = base64.urlsafe_b64decode(data.session_encrypted_dek + '=' * (4 - len(data.session_encrypted_dek) % 4))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid session_encrypted_dek format")
    
    # Create safe session (24 hours by default)
    session_id = create_safe_session(
        safe_id=data.safe_id,
        user_id=user["id"],
        encrypted_dek=session_encrypted_dek,
        expires_hours=24
    )
    
    return {
        "status": "ok",
        "session_id": session_id,
        "safe_id": data.safe_id,
        "message": "Safe unlocked successfully"
    }


@router.post("/{safe_id}/lock")
def lock_safe(safe_id: str, request: Request):
    """Lock a safe (invalidate all sessions)."""
    user = require_user(request)
    
    safe = get_safe(safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Delete all sessions for this safe and user
    db = get_db()
    db.execute(
        "DELETE FROM safe_sessions WHERE safe_id = ? AND user_id = ?",
        (safe_id, user["id"])
    )
    db.commit()
    
    return {"status": "ok", "message": "Safe locked"}


@router.get("/status/unlocked")
def get_unlocked_safes(request: Request):
    """Get list of currently unlocked safe IDs."""
    user = require_user(request)
    
    cleanup_expired_safe_sessions()
    unlocked = get_user_unlocked_safes(user["id"])
    
    return {"unlocked_safes": unlocked}


# =============================================================================
# Safe Content Key Endpoint (for file operations)
# =============================================================================

@router.get("/{safe_id}/key")
def get_safe_key(safe_id: str, request: Request):
    """Get encrypted safe key for file operations.
    
    Returns the session-encrypted DEK for client-side decryption.
    """
    user = require_user(request)
    
    safe = get_safe(safe_id)
    if not safe:
        raise HTTPException(status_code=404, detail="Safe not found")
    
    if safe["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if safe is unlocked
    if not is_safe_unlocked_for_user(safe_id, user["id"]):
        raise HTTPException(status_code=403, detail="Safe is locked. Please unlock first.")
    
    # Get the session to return encrypted DEK
    db = get_db()
    session = db.execute("""
        SELECT * FROM safe_sessions 
        WHERE safe_id = ? AND user_id = ? AND expires_at > datetime('now')
        ORDER BY created_at DESC
        LIMIT 1
    """, (safe_id, user["id"])).fetchone()
    
    if not session:
        raise HTTPException(status_code=403, detail="Safe session expired")
    
    return {
        "safe_id": safe_id,
        "session_id": session["id"],
        "encrypted_dek": base64.b64encode(session["encrypted_dek"]).decode(),
        "expires_at": session["expires_at"]
    }


# =============================================================================
# WebAuthn Credentials for Safe Setup
# =============================================================================

@router.get("/webauthn/credentials")
def list_webauthn_credentials_for_safes(request: Request):
    """List user's WebAuthn credentials that can be used for safe protection."""
    user = require_user(request)
    
    credentials = get_webauthn_credentials(user["id"])
    
    return {
        "credentials": [
            {
                "id": c["id"],
                "name": c["name"],
                "credential_id": base64.b64encode(c["credential_id"]).decode(),
                "created_at": c["created_at"]
            }
            for c in credentials
        ]
    }
