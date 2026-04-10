"""Centralized logging configuration for the application."""
import logging
import os
import sys


def setup_logging() -> None:
    """Configure application-wide logging.
    
    Uses SYNTH_LOG_LEVEL environment variable to control verbosity:
    - DEBUG: Detailed debug information (development)
    - INFO: General application flow (default)
    - WARNING: Only warnings and errors (production quiet mode)
    - ERROR: Only errors
    - CRITICAL: Only critical errors
    
    Also checks SYNTH_ENV=production to set defaults.
    """
    # Determine log level from environment
    env_level = os.environ.get("SYNTH_LOG_LEVEL", "").upper()
    env = os.environ.get("SYNTH_ENV", "development")
    
    if env_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = getattr(logging, env_level)
    elif env == "production":
        level = logging.WARNING  # Quiet in production by default
    else:
        level = logging.INFO  # Informative in development
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,  # Override any existing configuration
    )
    
    # Configure uvicorn loggers (HTTP access logs)
    if env == "production":
        # Quiet in production - no access logs
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    else:
        # In development: show access logs (requests, status codes, IPs)
        logging.getLogger("uvicorn").setLevel(logging.INFO)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given module name.
    
    Usage:
        from app.logging_config import get_logger
        
        logger = get_logger(__name__)
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.exception("Exception with traceback")
    """
    return logging.getLogger(name)
