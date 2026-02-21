# Synth Gallery - Agent Documentation

> **For AI Coding Agents**: This document provides essential context for working with the Synth Gallery codebase. Read this before making any changes.

## Project Overview

Synth Gallery is a **personal media vault** with end-to-end encryption, hardware key authentication, and multi-user support. It allows users to securely store, organize, and share photos and videos.

### Key Features
- **Server-side Encryption**: AES-256-GCM for all uploaded files (per-user keys)
- **Encrypted Vaults (Safes)**: Independent E2E-encrypted containers with separate keys (client-side encryption)
- **Hardware Key Login**: WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Folder Hierarchy**: Nested folders with sharing support (Viewer/Editor permissions)
- **Albums**: Group related media with drag-and-drop reordering
- **Tags & Search**: Categorize and find content quickly
- **Backup & Recovery**: Full backups with integrity verification and recovery keys

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Web Framework | FastAPI 0.129.0 |
| Server | Uvicorn 0.41.0 |
| Database | SQLite (sqlite3 module) |
| Templates | Jinja2 3.1.6 |
| Styling | Vanilla CSS |
| Frontend JS | Vanilla JavaScript (modular) |
| Image Processing | Pillow 12.1.1 |
| Video Processing | OpenCV 4.13.0.92 |
| Encryption | cryptography (AES-256-GCM) |
| Password Hashing | bcrypt via passlib |
| Hardware Keys | webauthn >=2.0.0 (FIDO2) |

## Project Structure

```
Synth-Gallery/
├── app/                          # Main application
│   ├── main.py                   # FastAPI entry point, lifespan management
│   ├── config.py                 # Configuration constants, environment variables
│   ├── database.py               # DB connections, schema init, password hashing
│   ├── middleware.py             # AuthMiddleware, CSRFMiddleware
│   ├── dependencies.py           # FastAPI dependencies (get_current_user, etc.)
│   ├── tags.py                   # Tag dictionary for photo suggestions
│   ├── application/              # Application services (business logic)
│   │   └── services/
│   │       ├── auth_service.py       # Authentication, sessions, DEK management
│   │       ├── folder_service.py     # Folder CRUD, tree operations
│   │       ├── permission_service.py # Access control logic
│   │       ├── photo_service.py      # Photo/album operations
│   │       ├── upload_service.py     # File uploads with encryption
│   │       ├── safe_service.py       # Encrypted vault operations
│   │       ├── safe_file_service.py  # File access in safes
│   │       ├── envelope_service.py   # Envelope encryption key management
│   │       └── user_settings_service.py # User preferences
│   ├── infrastructure/           # Infrastructure layer
│   │   ├── repositories/         # Repository pattern (DB operations)
│   │   │   ├── base.py               # Repository base class
│   │   │   ├── user_repository.py
│   │   │   ├── session_repository.py
│   │   │   ├── folder_repository.py
│   │   │   ├── permission_repository.py
│   │   │   ├── photo_repository.py
│   │   │   ├── safe_repository.py
│   │   │   └── webauthn_repository.py
│   │   └── services/             # Infrastructure services
│   │       ├── encryption.py         # AES-256-GCM encryption, DEK cache
│   │       ├── backup.py             # Backup/restore service + scheduler
│   │       ├── media.py              # Media processing
│   │       ├── metadata.py           # EXIF/metadata extraction
│   │       ├── thumbnail.py          # Thumbnail generation
│   │       └── webauthn.py           # Hardware key support
│   ├── routes/                   # API routes
│   │   ├── auth.py                   # Login/logout
│   │   ├── admin.py                  # Admin panel, backups
│   │   ├── api.py                    # AI service endpoints
│   │   ├── folders.py                # Folder management
│   │   ├── tags.py                   # Tag management
│   │   ├── webauthn.py               # Hardware key registration/auth
│   │   ├── safes.py                  # Safe (vault) management
│   │   ├── safe_files.py             # File operations in safes
│   │   ├── envelope.py               # Envelope encryption
│   │   └── gallery/                  # Gallery routes
│   │       ├── main.py               # Main gallery view
│   │       ├── albums.py             # Album operations
│   │       ├── photos.py             # Photo display
│   │       ├── files.py              # File serving
│   │       ├── uploads.py            # Upload handling
│   │       └── deps.py               # Gallery dependencies
│   ├── static/                   # Static assets
│   │   ├── style.css
│   │   └── js/
│   │       ├── core.js               # Core utilities
│   │       ├── init.js               # Initialization
│   │       ├── navigation.js         # Navigation
│   │       ├── upload.js             # Upload handling
│   │       ├── crypto/               # Client-side crypto (Safes)
│   │       └── gallery-*.js          # Gallery features
│   └── templates/                # Jinja2 templates
│       ├── base.html
│       ├── login.html
│       ├── gallery.html
│       ├── settings.html
│       ├── encryption_settings.html
│       ├── admin_backups.html
│       └── admin_maintenance.html
├── tests/                        # Test suite
│   ├── conftest.py               # pytest fixtures
│   ├── integration/              # Integration tests
│   └── unit/                     # Unit tests
├── scripts/                      # Utility scripts
│   └── manage_users.py           # CLI for user/backup management
├── uploads/                      # Uploaded files (encrypted)
├── thumbnails/                   # Generated thumbnails (encrypted)
├── backups/                      # Backup storage
├── gallery.db                    # SQLite database
├── requirements.txt              # Python dependencies
├── Dockerfile                    # Docker build
├── docker-compose.yml            # Docker Compose config
├── Start.bat                     # Windows startup script
└── AGENTS.md                     # This file
```

## Architecture Patterns

### 1. Repository Pattern
All database operations go through repository classes:

```python
# CORRECT - Use repositories
from app.infrastructure.repositories import UserRepository
from app.database import create_connection

db = create_connection()
try:
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
finally:
    db.close()

# INCORRECT - Don't use raw SQL in routes
# db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### 2. Service Layer
Business logic lives in application services:

```python
# Services orchestrate repositories and implement business rules
from app.application.services import FolderService
from app.infrastructure.repositories import FolderRepository, PermissionRepository

db = create_connection()
try:
    service = FolderService(
        folder_repo=FolderRepository(db),
        permission_repo=PermissionRepository(db)
    )
    folder = service.create_folder(name, user_id, parent_id)
finally:
    db.close()
```

### 3. Database Connection Management
**CRITICAL**: Always use `create_connection()` for new connections and close them:

```python
# CORRECT - Explicit connection with cleanup
db = create_connection()
try:
    # Use db...
    repo = UserRepository(db)
    user = repo.get_by_id(1)
finally:
    db.close()

# For middleware/lifespan where request context matters:
from app.database import get_db
# get_db() returns thread-local connection - DO NOT CLOSE
```

### 4. Encryption Architecture

```
User Password / Hardware Key
    │
    ▼
PBKDF2-SHA256 (600k iterations)
    │
    ▼
KEK (Key Encryption Key) ───┐
    │                        │
    ▼                        │
DEK (Data Encryption Key) ◄──┘
    │
    ├──► File 1: CK encrypted with DEK
    └──► File 2: CK encrypted with DEK
```

- **DEK (Data Encryption Key)**: Per-user, 256-bit random, cached in memory during session
- **KEK (Key Encryption Key)**: Derived from password via PBKDF2
- **Files**: Encrypted with AES-256-GCM (nonce + ciphertext stored)

## Build and Run Commands

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn app.main:app --reload --port 8000

# With custom base URL (subpath)
set SYNTH_BASE_URL=synth  # Windows
export SYNTH_BASE_URL=synth  # Linux/macOS
uvicorn app.main:app --reload --port 8000
```

### Windows (Start.bat)
```bash
# Windows startup script with configuration
Start.bat
```

### Docker

```bash
# Build and run
docker-compose up --build

# The app runs on port 8008 by default
```

## Testing

### Run Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/integration/test_auth.py

# Run with coverage
pytest --cov=app --cov-report=html
```

### Test Structure

- `conftest.py`: Contains fixtures for isolated test environments
- `integration/`: Integration tests for routes and workflows
- `unit/`: Unit tests for individual components

### Key Fixtures

- `client`: FastAPI TestClient with fresh database
- `authenticated_client`: Pre-authenticated client
- `test_user`: Created user with credentials
- `test_folder`: Created folder for testing
- `uploaded_photo`: Pre-uploaded test photo

## User Management (CLI)

```bash
# Create user
python scripts/manage_users.py add <username> <password> <display_name>

# List users
python scripts/manage_users.py list

# Change password (re-encrypts all data)
python scripts/manage_users.py passwd <username> <old_password> <new_password>

# Generate recovery key
python scripts/manage_users.py recovery-key <username> <password>

# Recover with recovery key
python scripts/manage_users.py recover <username> <recovery_key>

# Backup operations
python scripts/manage_users.py backup
python scripts/manage_users.py backup-list
python scripts/manage_users.py verify <filename>
python scripts/manage_users.py restore <filename>
```

## Configuration (Environment Variables)

| Variable | Description | Default |
|----------|-------------|---------|
| `SYNTH_BASE_URL` | Base URL subpath (e.g., "synth") | "" |
| `SYNTH_AI_API_KEY` | API key for AI service | None |
| `WEBAUTHN_RP_NAME` | WebAuthn display name | "Synth Gallery" |
| `BACKUP_PATH` | Backup directory path | `./backups` |
| `BACKUP_SCHEDULE` | `daily`, `weekly`, or `disabled` | `daily` |
| `BACKUP_ROTATION_COUNT` | Number of backups to keep | 5 |

## Code Style Guidelines

### Python

1. **Type Hints**: Use type hints for function signatures
   ```python
   def get_user(self, user_id: int) -> dict | None:
       ...
   ```

2. **Docstrings**: Use triple-quoted docstrings for modules and public functions
   ```python
   """Brief description.
   
   Longer description if needed.
   
   Args:
       param: Description
       
   Returns:
       Description of return value
   """
   ```

3. **Imports**: Group imports (stdlib, third-party, local)
   ```python
   # Standard library
   import sqlite3
   from datetime import datetime
   
   # Third-party
   from fastapi import FastAPI
   
   # Local
   from ..config import BASE_DIR
   ```

4. **Database Connections**: Always use explicit connection management
   ```python
   db = create_connection()
   try:
       # operations
   finally:
       db.close()
   ```

5. **Error Handling**: Be specific with exceptions
   ```python
   try:
       result = operation()
   except sqlite3.IntegrityError:
       # Handle specific case
   except Exception as e:
       # Log unexpected errors
       raise
   ```

### JavaScript

1. **Modules**: Use ES6 modules with explicit exports
   ```javascript
   // core.js
   export function utility() { ... }
   
   // consumer.js
   import { utility } from './core.js';
   ```

2. **Event Listeners**: Use delegated events where appropriate
3. **Crypto Operations**: Use Web Crypto API for client-side encryption

## Security Considerations

### Encryption
- All files are encrypted with AES-256-GCM
- DEKs are never stored plaintext (encrypted with KEK)
- PBKDF2 uses 600,000 iterations (OWASP recommendation)
- Safes use client-side encryption (true E2E)

### Session Management
- HTTP-only cookies for session tokens
- SameSite=Lax CSRF protection
- 7-day session expiry
- DEK cache matches session TTL

### CSRF Protection
- Double-submit cookie pattern
- Tokens required for POST/PUT/DELETE/PATCH
- Exemptions: login page, API with separate auth

### WebAuthn/Hardware Keys
- Keys are bound to origin (domain)
- Supports multiple keys per user
- ECDSA and RSA signature algorithms

### Important Security Notes

1. **HTTPS Required**: Web Crypto API requires secure context (HTTPS or localhost)
2. **Safe Passwords**: Safe passwords are independent of account passwords
3. **Recovery Keys**: Generate and store offline - lost key = lost data
4. **Backup Security**: Backups contain encrypted content but plaintext metadata

## Common Tasks

### Adding a New Route

1. Create route file in `app/routes/`
2. Use repository pattern for DB access
3. Add CSRF token to forms via `get_csrf_token(request)`
4. Include router in `app/main.py`

### Adding a Repository

1. Inherit from `Repository` base class
2. Implement CRUD operations
3. Add to `app/infrastructure/repositories/__init__.py`

### Adding a Service

1. Create in `app/application/services/`
2. Accept repositories in `__init__`
3. Add to `app/application/services/__init__.py`

### Database Schema Changes

1. Modify `init_db()` in `app/database.py`
2. Add migration logic if needed (pre-migration backup is automatic)
3. Update relevant repository methods

## Troubleshooting

### "No module named 'app'"
Run from project root, not from app directory.

### Database locked errors
Ensure you're using `create_connection()` and closing properly.

### Session/DEK not persisting
Check cookie settings and DEK cache TTL.

### WebAuthn not working
Must use HTTPS or localhost (Web Crypto API requirement).

## Base URL / Subpath Configuration

The application supports running under a subpath (e.g., `localhost/synth/`):

```bash
# Windows PowerShell
$env:SYNTH_BASE_URL = "synth"

# Windows CMD
set SYNTH_BASE_URL=synth

# Linux/macOS
export SYNTH_BASE_URL=synth
```

| SYNTH_BASE_URL | Resulting URL |
|----------------|---------------|
| (empty) | `http://localhost:8000/login` |
| `synth` | `http://localhost:8000/synth/login` |
| `gallery/v2` | `http://localhost:8000/gallery/v2/login` |

**Note**: No leading or trailing slashes.

---

*This document should be updated when architectural changes are made.*
