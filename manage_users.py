#!/usr/bin/env python3
"""
User management CLI for Synth Gallery.
Run this script to add, modify, or remove users.

Usage:
    python manage_users.py add <username> <password> <display_name>
    python manage_users.py list
    python manage_users.py delete <username>
    python manage_users.py passwd <username> <new_password>
    python manage_users.py rename <username> <new_display_name>
    python manage_users.py admin <username>       - grant admin rights
    python manage_users.py unadmin <username>     - revoke admin rights
    python manage_users.py encrypt-files <username> <password>  - encrypt user's files
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    mark_photo_encrypted
)
from app.config import UPLOADS_DIR, THUMBNAILS_DIR


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
    if len(args) < 2:
        print("Error: passwd requires <username> <new_password>")
        return 1

    username, new_password = args[0], args[1]

    if len(new_password) < 4:
        print("Error: Password must be at least 4 characters")
        return 1

    user = get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found")
        return 1

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


def main():
    if len(sys.argv) < 2:
        print_usage()
        return 1

    # Initialize database
    init_db()

    command = sys.argv[1].lower()
    args = sys.argv[2:]

    commands = {
        'add': cmd_add,
        'list': cmd_list,
        'delete': cmd_delete,
        'passwd': cmd_passwd,
        'rename': cmd_rename,
        'admin': cmd_admin,
        'unadmin': cmd_unadmin,
        'encrypt-files': cmd_encrypt_files,
        'help': lambda _: (print_usage(), 0)[1],
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print_usage()
        return 1

    return commands[command](args)


if __name__ == "__main__":
    sys.exit(main())
