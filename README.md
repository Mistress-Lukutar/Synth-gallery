# Synth

Personal media vault with end-to-end encryption, hardware key authentication, and multi-user support.

## Key Features

### Security
- **Server-side Encryption** — AES-256-GCM encryption for all uploaded files
- **Encrypted Vaults (Safes)** — Independent E2E-encrypted containers with separate keys
- **Hardware Key Login** — WebAuthn/FIDO2 support (YubiKey, etc.) for passwordless authentication
- **Recovery Keys** — Generate backup keys to recover access if password is lost

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

### Windows (Recommended)

```bash
Start.bat
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

## First Run

On first startup, if no users exist, a temporary admin account is created:
- **Username:** admin
- **Password:** admin

**Important:** Log in with these credentials, then immediately create a new admin user and delete the temporary account.

## User Management

User management is available through the web UI at `/admin/users`:
- Create, edit, delete users
- Grant/revoke admin rights
- View user list

Profile settings (change password, recovery key, display name) are available at `/settings`.

## Backup & Restore

Admin UI available at `/admin/backups`:
- Create full backups (database + media files)
- Schedule automatic backups (daily/weekly)
- Verify backup integrity
- Download and restore from backups

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
# WebAuthn display name
WEBAUTHN_RP_NAME=Synth Gallery

# Backup settings
BACKUP_PATH=/path/to/backups
BACKUP_SCHEDULE=daily          # daily, weekly, or disabled
BACKUP_ROTATION_COUNT=5        # number of backups to keep

# Storage backend (optional)
STORAGE_BACKEND=local          # local or s3
S3_BUCKET=your-bucket
S3_REGION=us-east-1
S3_ENDPOINT=https://s3.amazonaws.com  # for MinIO/custom endpoints
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
```

## Security Model

| Layer          | Protection                                      |
|----------------|-------------------------------------------------|
| Files at rest  | AES-256-GCM per-user encryption                 |
| Safes (Vaults) | Additional AES-256-GCM with independent keys    |
| Password       | bcrypt + PBKDF2-SHA256 key derivation           |
| Sessions       | HTTP-only cookies, 7-day expiry                 |
| API            | CSRF tokens                                    |
| Login          | Password or WebAuthn hardware keys              |

## Tech Stack

- Python 3.11 / FastAPI
- SQLite
- Jinja2 templates
- Pillow / OpenCV (media processing)
- cryptography (AES-256-GCM)
- py_webauthn (FIDO2)

## License

MIT
