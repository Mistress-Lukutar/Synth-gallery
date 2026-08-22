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

#### Server-Side Encryption (All Media)
| Component                 | Algorithm              | Details                                   |
|---------------------------|------------------------|-------------------------------------------|
| File Encryption           | **AES-256-GCM**        | Chunked AEAD envelope (SGE1)              |
| Chunk Size                | **1 MiB**              | Configurable via `SYNTH_ENCRYPTION_CHUNK_SIZE` |
| Key Encryption Key (KEK)  | **PBKDF2-HMAC-SHA256** | 600,000 iterations (OWASP recommendation) |
| Data Encryption Key (DEK) | **256-bit random**     | Generated via `os.urandom(32)`            |
| Salt                      | **256-bit**            | 32 bytes per-user                         |
| Nonce/IV                  | **96-bit**             | 12 bytes, unique per chunk                |
| Password Hashing          | **bcrypt**             | Adaptive hashing with automatic salt      |
| Recovery Keys             | **256-bit**            | Base64url-encoded, 43 characters          |

#### Chunked AEAD Envelope (SGE1)

Every stored file (uploads, thumbnails, JXL fallbacks) uses the same chunked
format so a single code path handles small thumbnails and multi-GiB videos:

```
[MAGIC "SGE1" 4B][VERSION 1B][RESERVED 1B][CHUNK_SIZE 4B BE]
for each plaintext chunk (CHUNK_SIZE bytes, last may be shorter):
    [NONCE 12B][CIPHERTEXT + 16B GCM TAG]
```

- Each chunk is independently decryptable (own nonce + tag), so HTTP Range
  requests decrypt only the chunks overlapping the requested byte range
- Memory usage is O(CHUNK_SIZE) regardless of file size — full plaintext is
  never held in memory

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
Cookie Name: __Host-synth_session
Cookie Flags:
  - HttpOnly: ✅ Prevents XSS access
  - SameSite=Lax: ✅ CSRF protection
  - Secure: ✅ Default (COOKIE_SECURE=true); __Host- prefix enforces
           Secure, Path=/ and no Domain attribute at browser level

Session TTL: 7 days (604,800 seconds)
DEK Cache TTL: Matches session (7 days)
```

### CSRF Protection

- **Double-submit cookie pattern**: CSRF token in `__Host-synth_csrf` cookie + header
- **Token generation**: `secrets.token_urlsafe(32)` (256-bit entropy)
- **Protected methods**: POST, PUT, DELETE, PATCH
- **Exemptions**: Login page (before session establishment), API endpoints with separate auth

### Rate Limiting & Security Headers

- **Built-in `RateLimitMiddleware`**: per-endpoint request rate limits
- **`SecurityHeadersMiddleware`**: CSP, HSTS, X-Frame-Options and related headers
- **Audit logging**: security events tracked via `AuditLogService`
- **Session fingerprinting**: browser-fingerprint based hijack detection

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
│       ├──► File 1: SGE1 chunked envelope encrypted with DEK │
│       └──► File 2: SGE1 chunked envelope encrypted with DEK │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Storage Security

| Data Type       | Storage     | Protection                         |
|-----------------|-------------|------------------------------------|
| Password hashes | SQLite      | bcrypt hashed                      |
| Encrypted DEKs  | SQLite      | AES-256-GCM encrypted with KEK     |
| File content    | Filesystem  | SGE1 chunked AES-256-GCM           |
| Session tokens  | SQLite      | Random tokens, 7-day expiry        |
| Thumbnails      | Filesystem  | Encrypted at rest (same envelope)  |

### Backup Security

- **Integrity verification**: SHA-256 checksums for all files
- **Manifest**: JSON with version, timestamps, checksums, user list
- **Format**: ZIP archive with database and encrypted uploads
- **Automatic rotation**: Configurable (default: 5 backups)
- **No encryption of backup itself**: Relying on filesystem encryption (data already encrypted at rest)

## Security Best Practices for Users

### Deployment

1. **Always use HTTPS in production**
   - Secure cookies (`__Host-` prefix) are only sent over HTTPS
   - Set `COOKIE_SECURE=false` only for local HTTP development

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

2. **Store recovery keys offline**
   - Print or write down recovery keys
   - Store in physically secure location
   - Lost password + lost recovery key = lost data

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

2. **Server-side encryption model**
   - Files are decrypted server-side for serving and thumbnail generation
   - The server operator (or anyone with memory access during a session)
     can technically access plaintext — this is not end-to-end encryption

3. **Folder sharing limitations**
   - Revoking folder access does NOT re-encrypt existing files
   - True revocation requires manual re-encryption

4. **Backup integrity**
   - Backups are not encrypted (content is already encrypted)
   - Backup metadata (manifest) is plaintext

## Security Audit Checklist

- [ ] HTTPS enabled in production
- [ ] `COOKIE_SECURE` not disabled in production
- [ ] Database file permissions restricted
- [ ] Backup directory on encrypted filesystem
- [ ] Session cookie secure flags verified
- [ ] CSRF protection tested
- [ ] WebAuthn origin validation working
- [ ] Rate limiting tuned for your reverse proxy setup

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

**Last Updated**: 2026-08-22  
**Policy Version**: 2.0
