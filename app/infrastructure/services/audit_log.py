"""Audit logging for security events.

Logs to a dedicated file (logs/security.log) using Python's logging module.
All events are written as JSON lines for easy parsing.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Ensure log directory exists
LOG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Configure security logger
logger = logging.getLogger("synth_security")
logger.setLevel(logging.INFO)

# Prevent duplicate handlers if module is reloaded
if not logger.handlers:
    handler = logging.FileHandler(LOG_DIR / "security.log", encoding="utf-8")
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event_type: str, **kwargs) -> None:
    record = {
        "timestamp": _now(),
        "event": event_type,
    }
    record.update(kwargs)
    logger.info(json.dumps(record, default=str))


def log_failed_login(username: str, ip: str | None, user_agent: str | None) -> None:
    _log("failed_login", username=username, ip=ip, user_agent=user_agent)


def log_successful_login(user_id: int, username: str, ip: str | None) -> None:
    _log("successful_login", user_id=user_id, username=username, ip=ip)


def log_logout(user_id: int, ip: str | None) -> None:
    _log("logout", user_id=user_id, ip=ip)


def log_password_reset(user_id: int, ip: str | None) -> None:
    _log("password_reset", user_id=user_id, ip=ip)


def log_session_hijack_detected(
    session_id: str,
    user_id: int,
    ip: str | None,
    user_agent: str | None
) -> None:
    _log("session_hijack_detected", session_id=session_id, user_id=user_id, ip=ip, user_agent=user_agent)


def log_api_key_created(key_id: int, key_name: str, admin_id: int, user_id: int) -> None:
    _log("api_key_created", key_id=key_id, key_name=key_name, admin_id=admin_id, user_id=user_id)


def log_api_key_revoked(key_id: int, admin_id: int) -> None:
    _log("api_key_revoked", key_id=key_id, admin_id=admin_id)


def log_api_key_failure(ip: str | None, reason: str) -> None:
    _log("api_key_failure", ip=ip, reason=reason)


def log_ai_job_claimed(key_id: int, job_id: int, item_id: str) -> None:
    _log("ai_job_claimed", key_id=key_id, job_id=job_id, item_id=item_id)
