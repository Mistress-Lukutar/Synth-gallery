'''
File:   config.py
Brief:  Application configuration and constants.
Author: Mistress-Lukutar
Date:   2026-07-21
Version: v0.2.0
'''

import os
from pathlib import Path

from app.logging_config import setup_logging

# Initialize logging configuration.
setup_logging()

# Directory paths.
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"

# Create directories if they don't exist.
UPLOADS_DIR.mkdir(exist_ok=True)
THUMBNAILS_DIR.mkdir(exist_ok=True)

# Base URL configuration (for running under a subpath like /synth).
# Set via environment variable SYNTH_BASE_URL, e.g. "synth" or "/synth".
BASE_URL = os.environ.get("SYNTH_BASE_URL", "").strip("/")
ROOT_PATH = f"/{BASE_URL}" if BASE_URL else ""

# Allowed media types.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/jxl",
}
ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/webm",
    "video/x-matroska",  # MKV container
    "video/x-mkv",       # Common MKV MIME variant
}
ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

# Encryption (chunked AES-256-GCM streaming format).
# Each chunk carries its own 12-byte nonce and 16-byte GCM tag, enabling
# O(1) memory encryption/decryption and HTTP Range serving.
ENCRYPTION_CHUNK_SIZE = int(
    os.environ.get("SYNTH_ENCRYPTION_CHUNK_SIZE", str(1 << 20))
)  # 1 MiB default

# Session configuration
# __Host- prefix enforces Secure, Path=/ and no Domain attribute at browser level
SESSION_COOKIE = "__Host-synth_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that don't require authentication (without BASE_URL prefix)
PUBLIC_PATHS = {
    "/login",
    "/static",
    "/favicon.ico",
    "/api/auth/recover",
    "/reset-password"
}

# CSRF configuration
CSRF_TOKEN_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_NAME = "__Host-synth_csrf"

# Backup configuration
BACKUP_PATH = Path(os.environ.get("BACKUP_PATH", str(BASE_DIR / "backups")))
BACKUP_PATH.mkdir(exist_ok=True)
BACKUP_ROTATION_COUNT = int(os.environ.get("BACKUP_ROTATION_COUNT", "5"))
BACKUP_SCHEDULE = os.environ.get("BACKUP_SCHEDULE", "daily")  # daily, weekly, or disabled
BACKUP_ENCRYPTION_KEY = os.environ.get("SYNTH_BACKUP_KEY", "")

# Tag stats scheduler configuration
TAG_STATS_SCHEDULE = os.environ.get("TAG_STATS_SCHEDULE", "weekly")  # daily, weekly, or disabled
TAG_STATS_HOUR = int(os.environ.get("TAG_STATS_HOUR", "3"))

# External host configuration (for generating shareable links)
EXTERNAL_HOST = os.environ.get("SYNTH_EXTERNAL_HOST", "").strip("/")

# WebAuthn configuration
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "Synth Gallery")

# Cookie security settings
# Default is secure (HTTPS only). Set COOKIE_SECURE=false for HTTP dev environments.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

# JPEG XL experimental storage settings
# Set USE_JXL=true to transcode new image uploads to lossless JPEG XL.
USE_JXL = os.environ.get("USE_JXL", "false").lower() == "true"
JXL_FALLBACK_QUALITY = int(os.environ.get("JXL_FALLBACK_QUALITY", "85"))
JXL_LOSSLESS_TRANSCODE_JPEG = (
    os.environ.get("JXL_LOSSLESS_TRANSCODE_JPEG", "true").lower() == "true"
)

# JPEG XL encoder tuning.
# effort: 1 (fastest/largest) to 9 (slowest/smallest). Default 7.
JXL_EFFORT = int(os.environ.get("JXL_EFFORT", "7"))

# num_threads passed to the JXL encoder. -1 lets the encoder decide.
JXL_THREADS = int(os.environ.get("JXL_THREADS", "-1"))

# Progressive encoding flags for cjxl.
# --progressive_ac and --qprogressive_ac improve perceived loading speed.
# --progressive_dc=1 adds an extra 64x64 low-resolution pass; -1 disables it.
JXL_PROGRESSIVE_AC = (
    os.environ.get("JXL_PROGRESSIVE_AC", "true").lower() == "true"
)
JXL_QPROGRESSIVE_AC = (
    os.environ.get("JXL_QPROGRESSIVE_AC", "true").lower() == "true"
)
JXL_PROGRESSIVE_DC = int(os.environ.get("JXL_PROGRESSIVE_DC", "1"))

# Cache directory for on-demand JPEG fallbacks generated from JXL originals
FALLBACKS_DIR = BASE_DIR / "fallbacks"
FALLBACKS_DIR.mkdir(exist_ok=True)
