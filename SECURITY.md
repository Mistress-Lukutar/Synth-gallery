# Security Policy

## Supported Versions

Security updates are provided for the latest stable version (main branch).

## Reporting a Vulnerability

**Please DO NOT create a public issue for security vulnerabilities.**

Instead, contact us privately:

- GitHub: [https://github.com/Mistress-Lukutar](https://github.com/Mistress-Lukutar)
- Please use [GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) if enabled

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Proposed fix (if available)
- Your contact information for follow-up

### Response Timeline

- **Acknowledgment**: 24-48 hours
- **Initial assessment**: 7 days
- **Fix and disclosure**: 30-90 days depending on complexity

## Security Architecture

### Encryption Algorithms

#### Server-Side Encryption (Standard Media)
| Component                 | Algorithm              | Details                                   |
|---------------------------|------------------------|-------------------------------------------|
| File Encryption           | **AES-256-GCM**        | Authenticated encryption (AEAD)           |
| Key Encryption Key (KEK)  | **PBKDF2-HMAC-SHA256** | 600,000 iterations (OWASP recommendation) |
| Data Encryption Key (DEK) | **256-bit random**     | Generated via `os.urandom(32)`            |
| Salt                      | **256-bit**            | 32 bytes per-user                         |
| Nonce/IV                  | **96-bit**             | 12 bytes, unique per encryption           |
| Password Hashing          | **bcrypt**             | Adaptive hashing with automatic salt      |
| Recovery Keys             | **256-bit**            | Base64url-encoded, 43 characters          |

#### Client-Side Encryption (Safes/Vaults)
| Component       | Algorithm              | Details                                   |
|-----------------|------------------------|-------------------------------------------|
| File Encryption | **AES-256-GCM**        | Via Web Crypto API                        |
| Key Derivation  | **PBKDF2-HMAC-SHA256** | 600,000 iterations                        |
| Safe DEK        | **AES-256-GCM**        | Generated via `crypto.subtle.generateKey` |
| Session Key     | **256-bit random**     | Ephemeral, memory-only storage            |

#### WebAuthn / FIDO2 Authentication
| Component            | Algorithm                          | Details                                  |
|----------------------|------------------------------------|------------------------------------------|
| Signature Algorithms | **ECDSA with SHA-256**             | COSE Algorithm -7                        |
|                      | **RSASSA-PKCS1-v1_5 with SHA-256** | COSE Algorithm -257                      |
| Challenge Storage    | **Ephemeral**                      | 5-minute expiration                      |
| User Verification    | **Preferred**                      | Supports PIN/biometrics on hardware keys |

### Session Security

```
Session Token: cryptographically secure random (256-bit entropy)
Cookie Flags:
  - HttpOnly: ✅ Prevents XSS access
  - SameSite=Lax: ✅ CSRF protection
  - Secure: Implicit via context
  
Session TTL: 7 days (604,800 seconds)
DEK Cache TTL: Matches session (7 days)
```

### CSRF Protection

- **Double-submit cookie pattern**: CSRF token in cookie + header/form
- **Token generation**: `secrets.token_urlsafe(32)` (256-bit entropy)
- **Protected methods**: POST, PUT, DELETE, PATCH
- **Exemptions**: Login page (before session establishment), API endpoints with separate auth

### Key Management Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ENCRYPTION HIERARCHY                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  USER PASSWORD / HARDWARE KEY                               │
│       │                                                     │
│       ▼                                                     │
│  PBKDF2-SHA256 (600k iterations)                            │
│       │                                                     │
│       ▼                                                     │
│  KEK (Key Encryption Key) ─────────┐                        │
│       │                            │                        │
│       ▼                            │                        │
│  DEK (Data Encryption Key) ◄───────┘                        │
│       │                                                     │
│       ├──► File 1: CK encrypted with DEK                    │
│       ├──► File 2: CK encrypted with DEK                    │
│       └──► Safe DEK: encrypted with password/hardware key   │
│                                                             │
│  Safes (E2E): Content encrypted with Safe DEK               │
│               Safe DEK never leaves browser memory          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Storage Security

| Data Type       | Storage     | Protection                         |
|-----------------|-------------|------------------------------------|
| Password hashes | SQLite      | bcrypt hashed                      |
| Encrypted DEKs  | SQLite      | AES-256-GCM encrypted with KEK     |
| File content    | Filesystem  | AES-256-GCM encrypted              |
| Session tokens  | SQLite      | Random tokens, 7-day expiry        |
| Safe DEKs       | Memory only | Never persisted server-side        |
| Thumbnails      | Filesystem  | Regenerated from encrypted content |

### Backup Security

- **Integrity verification**: SHA-256 checksums for all files
- **Manifest**: JSON with version, timestamps, checksums, user list
- **Format**: ZIP archive with database and encrypted uploads
- **Automatic rotation**: Configurable (default: 5 backups)
- **No encryption of backup itself**: Relying on filesystem encryption (data already encrypted at rest)

## Security Best Practices for Users

### Deployment

1. **Always use HTTPS in production**
   - Web Crypto API requires secure context (HTTPS or localhost)
   - Safes will not work without HTTPS

2. **Protect the database file**
   - `gallery.db` contains encrypted keys but should still be protected
   - Set appropriate filesystem permissions (600 or 640)

3. **Secure the backup directory**
   - Backups contain all encrypted data
   - Store on encrypted filesystem or separate secure location

### Passwords

1. **Use strong passwords**
   - Minimum 12 characters recommended
   - Mix of uppercase, lowercase, numbers, symbols

2. **Safe passwords are independent**
   - Safe passwords are NOT the same as account passwords
   - Lost Safe password = lost data (no recovery possible)

3. **Store recovery keys offline**
   - Print or write down recovery keys
   - Store in physically secure location

### Hardware Keys (WebAuthn)

1. **Register separate keys per domain**
   - Keys are bound to the origin (localhost, IP, or domain)
   - Register different keys for local access vs. public domain

2. **Have backup credentials**
   - Register multiple hardware keys, or
   - Keep password login enabled as fallback

## Known Limitations

1. **No forward secrecy**
   - If DEK is compromised, all past and future files are at risk
   - Mitigation: DEK is only in memory during active session

2. **Server-side encryption for standard files**
   - Files are decrypted server-side for thumbnail generation
   - Use **Safes** for true end-to-end encryption

3. **Folder sharing limitations**
   - Revoking folder access does NOT re-encrypt existing files
   - True revocation requires manual re-encryption

4. **Backup integrity**
   - Backups are not encrypted (content is already encrypted)
   - Backup metadata (manifest) is plaintext

## Security Audit Checklist

- [ ] HTTPS enabled in production
- [ ] Database file permissions restricted
- [ ] Backup directory on encrypted filesystem
- [ ] Session cookie secure flags verified
- [ ] CSRF protection tested
- [ ] WebAuthn origin validation working
- [ ] Safe password recovery warnings displayed
- [ ] Rate limiting enabled (if using reverse proxy)

## Vulnerability Disclosure Policy

We follow responsible disclosure:

1. Reporter submits vulnerability privately
2. We acknowledge within 48 hours
3. We investigate and develop fix
4. Fix is deployed and reporter is notified
5. Public disclosure after 30 days (coordinated)

## Acknowledgments

We thank the following security researchers for responsible disclosure:

*No reported vulnerabilities yet*

---

**Last Updated**: 2026-02-16  
**Policy Version**: 1.0
