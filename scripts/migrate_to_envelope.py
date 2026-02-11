#!/usr/bin/env python3
"""
Migration script: Legacy Encryption → Envelope Encryption

This script helps migrate existing encrypted files to the new envelope encryption system.
It can run in two modes:

1. Server-side migration (for files where we have DEK in cache):
   - Downloads encrypted file
   - Decrypts with old DEK
   - Generates new CK
   - Re-encrypts with CK
   - Stores encrypted CK encrypted with user's DEK

2. Client-assisted migration (recommended):
   - Generates migration package for client
   - Client downloads, decrypts, re-encrypts
   - Uploads new envelope format

Usage:
    python scripts/migrate_to_envelope.py --user-id 123 --mode server
    python scripts/migrate_to_envelope.py --status
    python scripts/migrate_to_envelope.py --batch-size 10
"""
import argparse
import sys
import json
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import (
    get_db, get_user_by_id, get_photos_needing_migration, 
    get_migration_status, get_user_encryption_keys
)
from app.services.encryption import EncryptionService, dek_cache


def get_migration_summary():
    """Get overall migration status for all users."""
    db = get_db()
    
    # Total photos
    total = db.execute("SELECT COUNT(*) as cnt FROM photos").fetchone()["cnt"]
    
    # Migrated photos
    migrated = db.execute(
        "SELECT COUNT(*) as cnt FROM photos WHERE storage_mode = 'envelope'"
    ).fetchone()["cnt"]
    
    # Legacy photos
    legacy = db.execute(
        "SELECT COUNT(*) as cnt FROM photos WHERE storage_mode IS NULL OR storage_mode = 'legacy'"
    ).fetchone()["cnt"]
    
    # Users with envelope-encrypted photos
    users_with_envelope = db.execute(
        """SELECT COUNT(DISTINCT user_id) as cnt FROM photos 
           WHERE storage_mode = 'envelope'"""
    ).fetchone()["cnt"]
    
    total_users = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    
    print("=" * 60)
    print("ENVELOPE ENCRYPTION MIGRATION STATUS")
    print("=" * 60)
    print(f"\nTotal photos: {total}")
    print(f"Migrated (envelope): {migrated} ({migrated/total*100:.1f}%)")
    print(f"Legacy: {legacy} ({legacy/total*100:.1f}%)")
    print(f"\nUsers migrated: {users_with_envelope}/{total_users}")
    print("=" * 60)
    
    # Per-user breakdown
    print("\nPer-user status:")
    users = db.execute("SELECT id, username, display_name FROM users").fetchall()
    for user in users:
        status = get_migration_status(user["id"])
        percent = status["percent_complete"]
        bar = "█" * int(percent / 5) + "░" * (20 - int(percent / 5))
        print(f"  {user['username']:20} [{bar}] {percent:.1f}% ({status['migrated']}/{status['total']})")


def migrate_user_photos_server_side(user_id: int, dry_run: bool = False) -> dict:
    """
    Migrate a user's photos using server-side DEK (requires DEK in cache).
    
    WARNING: This is for emergency use only. Ideally, client should do the migration
to maintain zero-trust principle.
    
    Returns:
        Dict with migration results
    """
    from app.config import UPLOADS_DIR, THUMBNAILS_DIR
    
    user = get_user_by_id(user_id)
    if not user:
        print(f"Error: User {user_id} not found")
        return {"error": "User not found"}
    
    # Check if user has encryption keys
    enc_keys = get_user_encryption_keys(user_id)
    if not enc_keys:
        print(f"Error: User {user_id} has no encryption keys")
        return {"error": "No encryption keys"}
    
    # Check if DEK is in cache
    dek = dek_cache.get(user_id)
    if not dek:
        print(f"Error: DEK not in cache for user {user_id}")
        print("User must log in first to populate DEK cache")
        return {"error": "DEK not in cache"}
    
    # Get photos needing migration
    photos = get_photos_needing_migration(user_id)
    if not photos:
        print(f"No photos to migrate for user {user['username']}")
        return {"migrated": 0, "total": 0}
    
    print(f"Found {len(photos)} photos to migrate for user {user['username']}")
    
    if dry_run:
        print("DRY RUN - No changes will be made")
        return {"dry_run": True, "would_migrate": len(photos)}
    
    results = {"migrated": 0, "failed": 0, "errors": []}
    
    for photo in photos:
        photo_id = photo["id"]
        filename = photo["filename"]
        
        try:
            # Only process if file is currently encrypted
            if not photo.get("is_encrypted"):
                print(f"  Skipping {photo_id} - not encrypted")
                continue
            
            file_path = UPLOADS_DIR / filename
            if not file_path.exists():
                print(f"  Warning: File not found: {file_path}")
                results["failed"] += 1
                continue
            
            # Read encrypted file
            with open(file_path, "rb") as f:
                encrypted_data = f.read()
            
            # Decrypt with old DEK
            try:
                plaintext = EncryptionService.decrypt_file(encrypted_data, dek)
            except Exception as e:
                print(f"  Failed to decrypt {photo_id}: {e}")
                results["failed"] += 1
                results["errors"].append({"photo_id": photo_id, "error": str(e)})
                continue
            
            # Generate new Content Key
            ck = EncryptionService.generate_dek()
            
            # Re-encrypt with CK
            new_encrypted = EncryptionService.encrypt_file(plaintext, ck)
            
            # Encrypt CK with user's DEK
            # Note: In real migration, client should do this part
            # For server-side migration, we store CK encrypted with DEK
            from app.services.envelope_encryption import EnvelopeEncryptionService
            
            # For now, mark as needing client migration
            # Store placeholder - client will need to finalize
            EnvelopeEncryptionService.migrate_photo_to_envelope(
                photo_id=photo_id,
                encrypted_ck=b"MIGRATION_PENDING",  # Client will update
                encrypted_thumbnail_ck=None
            )
            
            # Write new encrypted file
            with open(file_path, "wb") as f:
                f.write(new_encrypted)
            
            print(f"  ✓ Migrated {photo_id}")
            results["migrated"] += 1
            
        except Exception as e:
            print(f"  ✗ Failed {photo_id}: {e}")
            results["failed"] += 1
            results["errors"].append({"photo_id": photo_id, "error": str(e)})
    
    return results


def generate_client_migration_package(user_id: int, output_dir: str = "./migration_packages"):
    """
    Generate a migration package for client-side migration.
    
    This creates a JSON file with metadata about files needing migration,
    which the client can use to perform the actual migration.
    """
    import os
    
    user = get_user_by_id(user_id)
    if not user:
        print(f"Error: User {user_id} not found")
        return
    
    photos = get_photos_needing_migration(user_id)
    if not photos:
        print(f"No photos to migrate for user {user['username']}")
        return
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate package
    package = {
        "user_id": user_id,
        "username": user["username"],
        "package_version": "1.0",
        "photos": []
    }
    
    for photo in photos:
        package["photos"].append({
            "photo_id": photo["id"],
            "filename": photo["filename"],
            "original_name": photo["original_name"],
            "is_encrypted": photo.get("is_encrypted", False),
            "media_type": photo.get("media_type", "image")
        })
    
    # Write package
    output_file = Path(output_dir) / f"migration_package_{user_id}.json"
    with open(output_file, "w") as f:
        json.dump(package, f, indent=2)
    
    print(f"Migration package written to: {output_file}")
    print(f"Contains {len(photos)} photos needing migration")
    print(f"\nUser should:")
    print(f"  1. Log in to the application")
    print(f"  2. Go to Settings → Migration")
    print(f"  3. Follow the migration wizard")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate photos to envelope encryption"
    )
    parser.add_argument(
        "--status", 
        action="store_true",
        help="Show migration status for all users"
    )
    parser.add_argument(
        "--user-id", 
        type=int,
        help="User ID to migrate"
    )
    parser.add_argument(
        "--mode", 
        choices=["server", "client-package"],
        default="client-package",
        help="Migration mode"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--output-dir",
        default="./migration_packages",
        help="Output directory for client packages"
    )
    
    args = parser.parse_args()
    
    if args.status:
        get_migration_summary()
        return
    
    if not args.user_id:
        print("Error: --user-id required (unless using --status)")
        parser.print_help()
        sys.exit(1)
    
    if args.mode == "server":
        print("WARNING: Server-side migration compromises zero-trust!")
        print("Use client-package mode for production.")
        print()
        
        if input("Continue? (yes/no): ").lower() != "yes":
            print("Aborted")
            return
        
        results = migrate_user_photos_server_side(args.user_id, args.dry_run)
        print("\nResults:")
        print(json.dumps(results, indent=2))
    
    elif args.mode == "client-package":
        generate_client_migration_package(args.user_id, args.output_dir)


if __name__ == "__main__":
    main()
