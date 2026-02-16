"""Application configuration and constants."""
import os
from pathlib import Path

# Directory paths
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

# Create directories if they don't exist
UPLOADS_DIR.mkdir(exist_ok=True)
THUMBNAILS_DIR.mkdir(exist_ok=True)

# Base URL configuration (for running under a subpath like /synth)
# Set via environment variable SYNTH_BASE_URL, e.g., "synth" or "/synth"
BASE_URL = os.environ.get("SYNTH_BASE_URL", "").strip("/")
ROOT_PATH = f"/{BASE_URL}" if BASE_URL else ""

# Allowed media types
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm"}
ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

# Session configuration
SESSION_COOKIE = "synth_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that don't require authentication (without BASE_URL prefix)
PUBLIC_PATHS = {"/login", "/static", "/favicon.ico"}

# API key for AI service (should be set via environment variable)
AI_API_KEY = os.environ.get("SYNTH_AI_API_KEY", None)

# CSRF configuration
CSRF_TOKEN_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_NAME = "synth_csrf"

# Backup configuration
BACKUP_PATH = Path(os.environ.get("BACKUP_PATH", str(BASE_DIR / "backups")))
BACKUP_PATH.mkdir(exist_ok=True)
BACKUP_ROTATION_COUNT = int(os.environ.get("BACKUP_ROTATION_COUNT", "5"))
BACKUP_SCHEDULE = os.environ.get("BACKUP_SCHEDULE", "daily")  # daily, weekly, or disabled

# WebAuthn configuration
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "Synth Gallery")
