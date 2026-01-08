"""WebAuthn service for hardware key authentication."""
import secrets
from typing import Optional

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    AuthenticatorAttachment,
    AttestationConveyancePreference,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from ..config import WEBAUTHN_RP_NAME


def get_rp_id_from_origin(origin: str) -> str:
    """Extract RP ID from origin URL.

    For localhost/IP addresses, use the hostname.
    For domain names, use the full domain.
    """
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    host = parsed.hostname or parsed.netloc.split(':')[0]
    return host


def get_origin_from_host(host: str, scheme: str = "http") -> str:
    """Build origin URL from Host header."""
    # Remove port for standard ports
    if ':' in host:
        hostname, port = host.rsplit(':', 1)
        if (scheme == "https" and port == "443") or (scheme == "http" and port == "80"):
            return f"{scheme}://{hostname}"
    return f"{scheme}://{host}"


class WebAuthnService:
    """Handles WebAuthn registration and authentication."""

    # Challenge storage (in production, use Redis or similar)
    # Key: challenge bytes, Value: (user_id, rp_id, origin, expires_at)
    _challenges: dict[bytes, tuple[int | None, str, str, float]] = {}

    @classmethod
    def generate_registration_options_for_user(
        cls,
        user_id: int,
        username: str,
        display_name: str,
        rp_id: str,
        origin: str,
        existing_credential_ids: list[bytes] = None
    ) -> tuple[dict, bytes]:
        """
        Generate registration options for a user to register a new credential.

        Returns:
            Tuple of (options_dict, challenge_bytes)
        """
        import time

        # Exclude existing credentials
        exclude_credentials = []
        if existing_credential_ids:
            exclude_credentials = [
                PublicKeyCredentialDescriptor(id=cred_id)
                for cred_id in existing_credential_ids
            ]

        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=WEBAUTHN_RP_NAME,
            user_id=str(user_id).encode(),
            user_name=username,
            user_display_name=display_name,
            exclude_credentials=exclude_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
            attestation=AttestationConveyancePreference.NONE,
        )

        # Store challenge with rp_id, origin, and expiration (5 minutes)
        challenge = options.challenge
        cls._challenges[challenge] = (user_id, rp_id, origin, time.time() + 300)

        return options_to_json(options), challenge

    @classmethod
    def verify_registration(
        cls,
        credential: dict,
        challenge: bytes,
        user_id: int
    ) -> tuple[bytes, bytes] | None:
        """
        Verify registration response from client.

        Returns:
            Tuple of (credential_id, public_key) or None if verification fails
        """
        import time

        # Verify challenge exists and hasn't expired
        stored = cls._challenges.get(challenge)
        if not stored:
            return None

        stored_user_id, rp_id, origin, expires_at = stored
        if time.time() > expires_at:
            del cls._challenges[challenge]
            return None

        if stored_user_id != user_id:
            return None

        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )

            # Clean up challenge
            del cls._challenges[challenge]

            return (
                verification.credential_id,
                verification.credential_public_key
            )
        except Exception as e:
            print(f"WebAuthn registration verification failed: {e}")
            return None

    @classmethod
    def generate_authentication_options_for_user(
        cls,
        user_id: int,
        credential_ids: list[bytes],
        rp_id: str,
        origin: str
    ) -> tuple[dict, bytes]:
        """
        Generate authentication options for a user with registered credentials.

        Returns:
            Tuple of (options_dict, challenge_bytes)
        """
        import time

        allow_credentials = [
            PublicKeyCredentialDescriptor(id=cred_id)
            for cred_id in credential_ids
        ]

        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        # Store challenge with rp_id, origin, and expiration (5 minutes)
        challenge = options.challenge
        cls._challenges[challenge] = (user_id, rp_id, origin, time.time() + 300)

        return options_to_json(options), challenge

    @classmethod
    def generate_authentication_options_discoverable(
        cls,
        rp_id: str,
        origin: str
    ) -> tuple[dict, bytes]:
        """
        Generate authentication options for discoverable credentials (passwordless).
        User doesn't need to enter username first.

        Returns:
            Tuple of (options_dict, challenge_bytes)
        """
        import time

        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=[],  # Empty = discoverable credentials
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        # Store challenge without user_id (will be determined from credential)
        challenge = options.challenge
        cls._challenges[challenge] = (None, rp_id, origin, time.time() + 300)

        return options_to_json(options), challenge

    @classmethod
    def verify_authentication(
        cls,
        credential: dict,
        challenge: bytes,
        credential_public_key: bytes,
        credential_current_sign_count: int
    ) -> int | None:
        """
        Verify authentication response from client.

        Returns:
            New sign count if verification succeeds, None otherwise
        """
        import time

        # Verify challenge exists and hasn't expired
        stored = cls._challenges.get(challenge)
        if not stored:
            return None

        _, rp_id, origin, expires_at = stored
        if time.time() > expires_at:
            del cls._challenges[challenge]
            return None

        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=credential_public_key,
                credential_current_sign_count=credential_current_sign_count,
            )

            # Clean up challenge
            del cls._challenges[challenge]

            return verification.new_sign_count
        except Exception as e:
            print(f"WebAuthn authentication verification failed: {e}")
            return None

    @classmethod
    def cleanup_expired_challenges(cls):
        """Remove expired challenges from storage."""
        import time
        now = time.time()
        expired = [ch for ch, (_, exp) in cls._challenges.items() if now > exp]
        for ch in expired:
            del cls._challenges[ch]
