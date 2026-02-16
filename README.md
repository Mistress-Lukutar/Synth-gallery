# Synth

Personal media vault with end-to-end encryption, hardware key authentication, and multi-user support.

## Key Features

### Security
- **End-to-End Encryption** — AES-256-GCM encryption for all uploaded files
- **Encrypted Vaults (Safes)** — Independent E2E-encrypted containers with separate keys
- **Hardware Key Login** — WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Recovery Keys** — Generate backup keys to recover access if password is lost
- **Zero-Knowledge** — Server/admin cannot access your files without your password

### Storage & Organization
- **Folder Hierarchy** — Organize content in nested folders
- **Albums** — Group related media with drag-and-drop reordering
- **Sharing** — Share folders with other users (Viewer/Editor permissions)
- **Tags & Search** — Categorize and find content quickly

### Backup & Recovery
- **Full Backups** — ZIP archives with database + encrypted files
- **Automatic Scheduling** — Daily/weekly backups with rotation
- **Integrity Verification** — SHA-256 checksums for all files

### Media Support
- Images: JPEG, PNG, GIF, WebP
- Videos: MP4, WebM
- Automatic thumbnail generation
- EXIF/metadata extraction

## Quick Start

### Docker (recommended)

```bash
docker-compose up --build
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

## User Management

```bash
# Create user
python manage_users.py add <username> <password> <display_name>

# Change password (re-encrypts all data)
python manage_users.py passwd <username> <old_password> <new_password>

# Generate recovery key
python manage_users.py recovery-key <username> <password>

# Recover access with recovery key
python manage_users.py recover <username> <recovery_key>

# List users
python manage_users.py list
```

## Backup & Restore

```bash
# Create full backup
python manage_users.py backup

# List backups
python manage_users.py backup-list

# Verify backup integrity
python manage_users.py verify <filename>

# Restore from backup
python manage_users.py restore <filename>
```

Admin UI available at `/admin/backups`

## Hardware Key Setup

1. Log in with your password
2. Go to Settings (gear icon)
3. Add a hardware key (YubiKey, etc.)
4. Future logins: enter username → click "Sign in with Hardware Key"

Note: Keys are bound to the domain where registered. Register separate keys for each access method (localhost, VPN, public domain).

## Encrypted Vaults (Safes)

Safes are independent encrypted containers with their own encryption keys, separate from your user account key.

### Features
- **Independent Encryption Key** — Each safe has its own DEK (Data Encryption Key)
- **Multiple Unlock Methods** — Password (PBKDF2) or hardware key (WebAuthn)
- **Folder Structure** — Create folders and albums inside safes
- **True E2E** — Server never sees decrypted content; files are decrypted in your browser
- **No Sharing** — Owner-only access (by design)

### Usage
1. Click "+" next to "Safes" in the sidebar
2. Choose unlock method: password or hardware key
3. Upload files — they are encrypted client-side before sending to server
4. Click the safe to unlock it (decryption happens locally in your browser)

### Security Notes
- Safe passwords are independent of your account password
- Lost safe password = lost data (no recovery)
- Files inside safes are stored double-encrypted: safe encryption + user's master encryption
- Requires HTTPS or localhost (Web Crypto API requirement)

## Environment Variables

```bash
# AI service API key (optional)
SYNTH_AI_API_KEY=your-secret-key

# WebAuthn display name
WEBAUTHN_RP_NAME=Synth Gallery

# Backup settings
BACKUP_PATH=/path/to/backups
BACKUP_SCHEDULE=daily          # daily, weekly, or disabled
BACKUP_ROTATION_COUNT=5        # number of backups to keep
```

## Security Model

| Layer | Protection |
|-------|------------|
| Files at rest | AES-256-GCM per-user encryption |
| Safes (Vaults) | Additional AES-256-GCM with independent keys |
| Password | bcrypt + PBKDF2-SHA256 key derivation |
| Sessions | HTTP-only cookies, 7-day expiry |
| API | CSRF tokens, API key auth for external services |
| Login | Password or WebAuthn hardware keys |

## Tech Stack

- Python 3.11 / FastAPI
- SQLite
- Jinja2 templates
- Pillow / OpenCV (media processing)
- cryptography (AES-256-GCM)
- py_webauthn (FIDO2)

## License

MIT
