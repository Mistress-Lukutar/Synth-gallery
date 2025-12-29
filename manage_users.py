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
    update_user_display_name
)


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

    print(f"{'ID':<5} {'Username':<20} {'Display Name':<30} {'Created'}")
    print("-" * 80)
    for user in users:
        print(f"{user['id']:<5} {user['username']:<20} {user['display_name']:<30} {user['created_at']}")
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
        'help': lambda _: (print_usage(), 0)[1],
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print_usage()
        return 1

    return commands[command](args)


if __name__ == "__main__":
    sys.exit(main())
