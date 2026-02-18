"""Safe (encrypted vault) management routes."""
import base64
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..database import create_connection
from ..infrastructure.repositories import SafeRepository, WebAuthnRepository
from ..application.services import SafeService
from ..dependencies import require_user

router = APIRouter(prefix="/api/safes", tags=["safes"])


def get_safe_service() -> SafeService:
    """Get configured SafeService instance."""
    db = create_connection()
    safe_repo = SafeRepository(db)
    return SafeService(safe_repo)


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


class SafeUnlockComplete(BaseModel):
    safe_id: str
    # For password unlock: encrypted DEK from client (to create session)
    # For WebAuthn: authentication response
    challenge: Optional[str] = None
    credential: Optional[dict] = None
    # Session-encrypted DEK from client
    session_encrypted_dek: str  # DEK encrypted with session key


class SafeRename(BaseModel):
    name: str


# =============================================================================
# Safe Management Endpoints
# =============================================================================

@router.get("")
def list_safes(request: Request):
    """Get all safes for current user with unlock status."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.list_safes(user["id"])


@router.post("")
def create_new_safe(request: Request, data: SafeCreate):
    """Create a new safe."""
    user = require_user(request)
    
    db = create_connection()
    try:
        # For WebAuthn type, verify credential belongs to user
        if data.unlock_type == 'webauthn' and data.credential_id:
            cred_id_bytes = base64.urlsafe_b64decode(
                data.credential_id + '=' * (4 - len(data.credential_id) % 4)
            )
            webauthn_repo = WebAuthnRepository(db)
            cred = webauthn_repo.get_by_credential_id(cred_id_bytes)
            if not cred or cred["user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Invalid credential")
        
        service = SafeService(SafeRepository(db))
        return service.create_safe(
            name=data.name,
            user_id=user["id"],
            unlock_type=data.unlock_type,
            encrypted_dek_b64=data.encrypted_dek,
            password=data.password,
            salt_b64=data.salt,
            credential_id_b64=data.credential_id
        )
    finally:
        db.close()


@router.get("/{safe_id}")
def get_safe_details(safe_id: str, request: Request):
    """Get safe details."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.get_safe_details(safe_id, user["id"])


@router.put("/{safe_id}")
def rename_safe(safe_id: str, data: SafeRename, request: Request):
    """Rename a safe."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.rename_safe(safe_id, data.name, user["id"])


@router.delete("/{safe_id}")
def delete_safe_route(safe_id: str, request: Request):
    """Delete a safe and all its contents."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.delete_safe(safe_id, user["id"])


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
    
    db = create_connection()
    try:
        service = SafeService(SafeRepository(db))
        safe = service.safe_repo.get_by_id(data.safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        
        if safe["unlock_type"] == 'password':
            # Return challenge data for client-side decryption
            print(f"[DEBUG] Safe {data.safe_id}: unlock_type={safe['unlock_type']}, "
                  f"has_salt={safe['salt'] is not None}, "
                  f"has_encrypted_dek={safe['encrypted_dek'] is not None}")
            
            return service.get_unlock_challenge(data.safe_id, user["id"])
        
        elif safe["unlock_type"] == 'webauthn':
            # Return WebAuthn challenge
            from ..services.webauthn import WebAuthnService, get_rp_id_from_origin, get_origin_from_host
            
            scheme = request.headers.get(
                "x-forwarded-proto", 
                "https" if request.url.scheme == "https" else "http"
            )
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
    finally:
        db.close()


@router.post("/unlock/complete")
def complete_safe_unlock(request: Request, data: SafeUnlockComplete):
    """Complete safe unlock and create session."""
    user = require_user(request)
    
    db = create_connection()
    try:
        service = SafeService(SafeRepository(db))
        
        safe = service.safe_repo.get_by_id(data.safe_id)
        if not safe:
            raise HTTPException(status_code=404, detail="Safe not found")
        
        if safe["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        
        if safe["unlock_type"] == 'webauthn':
            # Verify WebAuthn response
            from ..services.webauthn import WebAuthnService
            
            if not data.credential or not data.challenge:
                raise HTTPException(
                    status_code=400, 
                    detail="WebAuthn credential and challenge required"
                )
            
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
            
            webauthn_repo = WebAuthnRepository(db)
            cred = webauthn_repo.get_by_credential_id(credential_id)
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
            webauthn_repo.update_sign_count(credential_id, new_sign_count)
        
        # Complete unlock (create session)
        return service.complete_unlock(
            safe_id=data.safe_id,
            user_id=user["id"],
            session_encrypted_dek_b64=data.session_encrypted_dek
        )
    finally:
        db.close()


@router.post("/{safe_id}/lock")
def lock_safe(safe_id: str, request: Request):
    """Lock a safe (invalidate all sessions)."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.lock_safe(safe_id, user["id"])


@router.get("/status/unlocked")
def get_unlocked_safes(request: Request):
    """Get list of currently unlocked safe IDs."""
    user = require_user(request)
    service = get_safe_service()
    
    return service.get_unlocked_safes(user["id"])


# =============================================================================
# Safe Content Key Endpoint (for file operations)
# =============================================================================

@router.get("/{safe_id}/key")
def get_safe_key(safe_id: str, request: Request):
    """Get encrypted safe key for file operations.
    
    Returns the session-encrypted DEK for client-side decryption.
    """
    user = require_user(request)
    service = get_safe_service()
    
    return service.get_safe_key(safe_id, user["id"])


# =============================================================================
# WebAuthn Credentials for Safe Setup
# =============================================================================

@router.get("/webauthn/credentials")
def list_webauthn_credentials_for_safes(request: Request):
    """List user's WebAuthn credentials that can be used for safe protection."""
    user = require_user(request)
    
    db = create_connection()
    try:
        webauthn_repo = WebAuthnRepository(db)
        credentials = webauthn_repo.get_for_user(user["id"])
        
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
    finally:
        db.close()
