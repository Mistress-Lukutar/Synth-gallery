#!/usr/bin/env python3
"""
User management and backup CLI for Synth Gallery.

Usage:
    User management:
        python manage_users.py add <username> <password> <display_name>
        python manage_users.py list
        python manage_users.py delete <username>
        python manage_users.py passwd <username> <new_password>
        python manage_users.py rename <username> <new_display_name>
        python manage_users.py admin <username>       - grant admin rights
        python manage_users.py unadmin <username>     - revoke admin rights
        python manage_users.py encrypt-files <username> <password>

    Backup operations:
        python manage_users.py backup                  - create full backup
        python manage_users.py backup-list             - list all backups
        python manage_users.py restore <filename>      - restore from backup
        python manage_users.py verify <filename>       - verify backup integrity

    Recovery key:
        python manage_users.py recovery-key <username> <password>  - generate recovery key
        python manage_users.py recover <username> <recovery_key>   - recover with key
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path

from app.database import (
    init_db,
    create_user,
    list_users,
    get_user_by_username,
    delete_user,
    update_user_password,
    update_user_display_name,
    set_user_admin,
    is_user_admin,
    verify_password,
    get_user_encryption_keys,
    set_user_encryption_keys,
    get_user_unencrypted_photos,
    mark_photo_encrypted,
    set_recovery_encrypted_dek,
    get_recovery_encrypted_dek,
)
from app.config import UPLOADS_DIR, THUMBNAILS_DIR, BACKUP_PATH


def print_usage():
    print(__doc__)


def cmd_add(args):
    if len(args) < 3:
        print("Error: add requires <username> <password> <display_name>")
        print("Example: python manage_users.py add admin mypassword \"Admin User\"")
        return 1

    username, password, display_name = args[0], args[1], args[2]

    if len(password) < 4:
        print("Error: Password must be at least 4 characters")
        return 1

    existing = get_user_by_username(username)
    if existing:
        print(f"Error: User '{username}' already exists")
        return 1

    try:
        user_id = create_user(username, password, display_name)
        print(f"User '{username}' created successfully (ID: {user_id})")
        return 0
    except Exception as e:
        print(f"Error creating user: {e}")
        return 1


def cmd_list(args):
    users = list_users()
    if not users:
        print("No users found. Create one with: python manage_users.py add <username> <password> <display_name>")
        return 0

    print(f"{'ID':<5} {'Username':<20} {'Display Name':<25} {'Admin':<6} {'Created'}")
    print("-" * 90)
    for user in users:
        admin_flag = "Yes" if is_user_admin(user['id']) else ""
        print(f"{user['id']:<5} {user['username']:<20} {user['display_name']:<25} {admin_flag:<6} {user['created_at']}")
    return 0


def cmd_delete(args):
    if len(args) < 1:
        print("Error: delete requires <username>")
        return 1

    username = args[0]
    user = get_user_by_username(username)

    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    # Confirm deletion
    confirm = input(f"Delete user '{username}' ({user['display_name']})? [y/N]: ")
    if confirm.lower() != 'y':
        print("Cancelled")
        return 0

    delete_user(user['id'])
    print(f"User '{username}' deleted")
    return 0


def cmd_passwd(args):
    if len(args) < 3:
        print("Error: passwd requires <username> <old_password> <new_password>")
        return 1

    username, old_password, new_password = args[0], args[1], args[2]

    if len(new_password) < 4:
        print("Error: Password must be at least 4 characters")
        return 1

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    # Verify old password
    if not verify_password(old_password, user["password_hash"], user["password_salt"] if "password_salt" in user.keys() else None):
        print("Error: Old password is incorrect")
        return 1

    # Check if user has encryption keys
    enc_keys = get_user_encryption_keys(user["id"])
    if enc_keys:
        from app.services.encryption import EncryptionService
        # Decrypt DEK with old password
        old_kek = EncryptionService.derive_kek(old_password, enc_keys["dek_salt"])
        try:
            dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], old_kek)
        except Exception as e:
            print(f"Error: Failed to decrypt encryption key: {e}")
            return 1

        # Re-encrypt DEK with new password
        new_salt = EncryptionService.generate_salt()
        new_kek = EncryptionService.derive_kek(new_password, new_salt)
        new_encrypted_dek = EncryptionService.encrypt_dek(dek, new_kek)

        # Update password and encryption keys
        update_user_password(user['id'], new_password)
        set_user_encryption_keys(user["id"], new_encrypted_dek, new_salt)
        print(f"Password and encryption keys updated for '{username}'")
    else:
        # No encryption - just update password
        update_user_password(user['id'], new_password)
        print(f"Password updated for '{username}'")
    return 0


def cmd_rename(args):
    if len(args) < 2:
        print("Error: rename requires <username> <new_display_name>")
        return 1

    username, new_display_name = args[0], args[1]

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    update_user_display_name(user['id'], new_display_name)
    print(f"Display name for '{username}' changed to '{new_display_name}'")
    return 0


def cmd_admin(args):
    if len(args) < 1:
        print("Error: admin requires <username>")
        return 1

    username = args[0]
    user = get_user_by_username(username)

    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    if is_user_admin(user['id']):
        print(f"User '{username}' is already an admin")
        return 0

    set_user_admin(user['id'], True)
    print(f"User '{username}' is now an admin")
    return 0


def cmd_unadmin(args):
    if len(args) < 1:
        print("Error: unadmin requires <username>")
        return 1

    username = args[0]
    user = get_user_by_username(username)

    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    if not is_user_admin(user['id']):
        print(f"User '{username}' is not an admin")
        return 0

    set_user_admin(user['id'], False)
    print(f"Admin rights revoked from '{username}'")
    return 0


def cmd_encrypt_files(args):
    """Encrypt all unencrypted files for a user."""
    if len(args) < 2:
        print("Error: encrypt-files requires <username> <password>")
        print("Note: Password is needed to derive encryption key")
        return 1

    username, password = args[0], args[1]

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    # Verify password
    if not verify_password(password, user["password_hash"], user["password_salt"] or ""):
        print("Error: Invalid password")
        return 1

    from app.services.encryption import EncryptionService

    # Get or create DEK
    enc_keys = get_user_encryption_keys(user["id"])
    if enc_keys:
        # Decrypt existing DEK
        kek = EncryptionService.derive_kek(password, enc_keys["dek_salt"])
        try:
            dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], kek)
        except Exception:
            print("Error: Could not decrypt existing key. Password may be incorrect.")
            return 1
    else:
        # Generate new DEK
        print("Generating new encryption key for user...")
        dek = EncryptionService.generate_dek()
        salt = EncryptionService.generate_salt()
        kek = EncryptionService.derive_kek(password, salt)
        encrypted_dek = EncryptionService.encrypt_dek(dek, kek)
        set_user_encryption_keys(user["id"], encrypted_dek, salt)

    # Get unencrypted photos
    photos = get_user_unencrypted_photos(user["id"])

    if not photos:
        print("No unencrypted files found for this user")
        return 0

    print(f"Found {len(photos)} unencrypted files")
    confirm = input("Proceed with encryption? [y/N]: ")
    if confirm.lower() != 'y':
        print("Cancelled")
        return 0

    encrypted = 0
    failed = 0

    for photo in photos:
        try:
            # Encrypt original
            orig_path = UPLOADS_DIR / photo["filename"]
            if orig_path.exists():
                with open(orig_path, "rb") as f:
                    data = f.read()
                encrypted_data = EncryptionService.encrypt_file(data, dek)
                with open(orig_path, "wb") as f:
                    f.write(encrypted_data)

            # Encrypt thumbnail
            thumb_path = THUMBNAILS_DIR / f"{photo['id']}.jpg"
            if thumb_path.exists():
                with open(thumb_path, "rb") as f:
                    data = f.read()
                encrypted_data = EncryptionService.encrypt_file(data, dek)
                with open(thumb_path, "wb") as f:
                    f.write(encrypted_data)

            mark_photo_encrypted(photo["id"])
            encrypted += 1
            print(f"  Encrypted: {photo['id']}")

        except Exception as e:
            failed += 1
            print(f"  Failed: {photo['id']} - {e}")

    print(f"\nComplete: {encrypted} encrypted, {failed} failed")
    return 0


# =============================================================================
# Backup Commands
# =============================================================================

def cmd_backup(args):
    """Create a full backup of database and media files."""
    from app.services.backup import FullBackupService

    print("Creating full backup...")
    print(f"Backup path: {BACKUP_PATH}")

    def progress(current, total, message):
        pct = int(current / total * 100) if total > 0 else 0
        print(f"\r  [{pct:3d}%] {message}".ljust(60), end="", flush=True)

    result = FullBackupService.create_full_backup(progress_callback=progress)
    print()  # New line after progress

    if result["success"]:
        print(f"\nBackup created successfully!")
        print(f"  File: {result['filename']}")
        print(f"  Size: {result['size_human']}")
        print(f"  Files: {result['stats']['total_files']}")
        print(f"  Users: {', '.join(result['stats']['users'])}")
        return 0
    else:
        print(f"\nBackup failed: {result['error']}")
        return 1


def cmd_backup_list(args):
    """List all available backups."""
    from app.services.backup import FullBackupService

    backups = FullBackupService.list_full_backups()

    if not backups:
        print("No backups found.")
        print(f"Backup path: {BACKUP_PATH}")
        return 0

    print(f"{'Filename':<35} {'Size':<12} {'Created':<20} {'Status'}")
    print("-" * 85)

    for backup in backups:
        status = "OK" if backup.get("valid") else f"INVALID: {backup.get('error', 'Unknown')}"
        created = backup.get("created_at", "Unknown")[:19] if backup.get("created_at") else "Unknown"
        print(f"{backup['filename']:<35} {backup.get('size_human', 'N/A'):<12} {created:<20} {status}")

    return 0


def cmd_restore(args):
    """Restore from a backup file."""
    from app.services.backup import FullBackupService

    if len(args) < 1:
        print("Error: restore requires <filename>")
        print("Use 'backup-list' to see available backups")
        return 1

    filename = args[0]
    backup_path = BACKUP_PATH / filename

    if not backup_path.exists():
        print(f"Error: Backup file not found: {backup_path}")
        return 1

    # Verify first
    print(f"Verifying backup: {filename}")
    verification = FullBackupService.verify_full_backup(backup_path)

    if not verification["valid"]:
        print(f"Error: Backup verification failed!")
        if verification.get("errors"):
            for err in verification["errors"][:5]:
                print(f"  - {err}")
        return 1

    print(f"Backup valid: {verification['verified_files']} files verified")

    # Confirm restore
    confirm = input("\nWARNING: This will overwrite existing data!\nProceed with restore? [y/N]: ")
    if confirm.lower() != 'y':
        print("Cancelled")
        return 0

    def progress(current, total, message):
        pct = int(current / total * 100) if total > 0 else 0
        print(f"\r  [{pct:3d}%] {message}".ljust(60), end="", flush=True)

    print("\nRestoring...")
    result = FullBackupService.restore_full_backup(backup_path, progress_callback=progress)
    print()

    if result["success"]:
        print(f"\nRestore completed successfully!")
        print(f"  Restored files: {result['restored_files']}")
        print("\nIMPORTANT: Restart the application to apply changes.")
        return 0
    else:
        print(f"\nRestore failed: {result['error']}")
        return 1


def cmd_verify(args):
    """Verify backup integrity."""
    from app.services.backup import FullBackupService

    if len(args) < 1:
        print("Error: verify requires <filename>")
        return 1

    filename = args[0]
    backup_path = BACKUP_PATH / filename

    if not backup_path.exists():
        print(f"Error: Backup file not found: {backup_path}")
        return 1

    print(f"Verifying backup: {filename}")
    result = FullBackupService.verify_full_backup(backup_path)

    if result["valid"]:
        print(f"\nBackup is VALID")
        print(f"  Verified files: {result['verified_files']}/{result['total_files']}")
        if result.get("manifest"):
            manifest = result["manifest"]
            print(f"  Created: {manifest.get('created_at', 'Unknown')}")
            print(f"  Version: {manifest.get('synth_gallery_version', 'Unknown')}")
            if manifest.get("stats"):
                print(f"  Total size: {manifest['stats'].get('total_size_human', 'Unknown')}")
        return 0
    else:
        print(f"\nBackup is INVALID")
        print(f"  Error: {result.get('error', 'Unknown')}")
        if result.get("errors"):
            print("  Issues found:")
            for err in result["errors"]:
                print(f"    - {err}")
        return 1


# =============================================================================
# Recovery Key Commands
# =============================================================================

def cmd_recovery_key(args):
    """Generate a recovery key for a user."""
    if len(args) < 2:
        print("Error: recovery-key requires <username> <password>")
        return 1

    username, password = args[0], args[1]

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    # Verify password
    if not verify_password(password, user["password_hash"], user["password_salt"] or ""):
        print("Error: Invalid password")
        return 1

    from app.services.encryption import EncryptionService

    # Get existing DEK
    enc_keys = get_user_encryption_keys(user["id"])
    if not enc_keys:
        print("Error: User has no encryption keys. Encrypt files first.")
        return 1

    # Decrypt DEK with password
    try:
        kek = EncryptionService.derive_kek(password, enc_keys["dek_salt"])
        dek = EncryptionService.decrypt_dek(enc_keys["encrypted_dek"], kek)
    except Exception:
        print("Error: Could not decrypt encryption key. Password may be incorrect.")
        return 1

    # Check if recovery key already exists
    existing_recovery = get_recovery_encrypted_dek(user["id"])
    if existing_recovery:
        confirm = input("User already has a recovery key. Generate a new one? [y/N]: ")
        if confirm.lower() != 'y':
            print("Cancelled")
            return 0

    # Generate recovery key and encrypt DEK with it
    formatted_key, raw_key = EncryptionService.generate_recovery_key()
    recovery_encrypted_dek = EncryptionService.encrypt_dek_with_recovery_key(dek, raw_key)

    # Store in database
    set_recovery_encrypted_dek(user["id"], recovery_encrypted_dek)

    print("\n" + "=" * 60)
    print("RECOVERY KEY GENERATED")
    print("=" * 60)
    print(f"\nUser: {username}")
    print(f"\nRecovery Key:\n")
    print(f"  {formatted_key}")
    print("\n" + "-" * 60)
    print("IMPORTANT:")
    print("  - Save this key in a secure location!")
    print("  - This key is shown ONLY ONCE!")
    print("  - If you lose both your password AND this key,")
    print("    your encrypted files will be UNRECOVERABLE!")
    print("=" * 60 + "\n")

    return 0


def cmd_recover(args):
    """Recover user access using recovery key."""
    if len(args) < 2:
        print("Error: recover requires <username> <recovery_key>")
        print("The recovery key can include dashes or be pasted as one string.")
        return 1

    username = args[0]
    # Join remaining args in case key was split
    recovery_key = ''.join(args[1:])

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

    from app.services.encryption import EncryptionService

    # Get recovery-encrypted DEK
    recovery_encrypted_dek = get_recovery_encrypted_dek(user["id"])
    if not recovery_encrypted_dek:
        print("Error: No recovery key configured for this user.")
        return 1

    # Try to decrypt DEK with recovery key
    try:
        raw_key = EncryptionService.parse_recovery_key(recovery_key)
        dek = EncryptionService.decrypt_dek_with_recovery_key(recovery_encrypted_dek, raw_key)
    except Exception as e:
        print(f"Error: Invalid recovery key - {e}")
        return 1

    print("Recovery key valid! DEK recovered successfully.")

    # Ask for new password
    new_password = input("\nEnter new password: ")
    if len(new_password) < 4:
        print("Error: Password must be at least 4 characters")
        return 1

    confirm_password = input("Confirm new password: ")
    if new_password != confirm_password:
        print("Error: Passwords do not match")
        return 1

    # Re-encrypt DEK with new password
    salt = EncryptionService.generate_salt()
    kek = EncryptionService.derive_kek(new_password, salt)
    encrypted_dek = EncryptionService.encrypt_dek(dek, kek)

    # Update password and encryption keys
    update_user_password(user["id"], new_password)
    set_user_encryption_keys(user["id"], encrypted_dek, salt)

    print(f"\nPassword reset successfully for '{username}'!")
    print("You can now log in with your new password.")
    return 0


def main():
    if len(sys.argv) < 2:
        print_usage()
        return 1

    # Initialize database
    init_db()

    command = sys.argv[1].lower()
    args = sys.argv[2:]

    commands = {
        # User management
        'add': cmd_add,
        'list': cmd_list,
        'delete': cmd_delete,
        'passwd': cmd_passwd,
        'rename': cmd_rename,
        'admin': cmd_admin,
        'unadmin': cmd_unadmin,
        'encrypt-files': cmd_encrypt_files,
        # Backup operations
        'backup': cmd_backup,
        'backup-list': cmd_backup_list,
        'restore': cmd_restore,
        'verify': cmd_verify,
        # Recovery key
        'recovery-key': cmd_recovery_key,
        'recover': cmd_recover,
        # Help
        'help': lambda _: (print_usage(), 0)[1],
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print_usage()
        return 1

    return commands[command](args)


if __name__ == "__main__":
    sys.exit(main())
