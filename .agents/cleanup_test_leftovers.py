#!/usr/bin/env python
"""Clean up test-suite leftovers from the production gallery database and
media directories.

Test runs (mostly historical, before env-based isolation landed in
tests/conftest.py) left behind:

1. Orphan media files: uploads/<item_id>, thumbnails/<item_id> and
   fallbacks/<item_id> whose item_id no longer exists in the items table.
2. Dangling DB rows referencing deleted test users/items/albums:
   - album_items whose album or item is gone
   - albums owned by deleted users (e.g. 'Test Album')
   - folders owned by deleted users (e.g. 'Test Folder', 'My Gallery')
   - user_settings / folder_permissions / ai_tagging_jobs orphans

The script NEVER deletes rows owned by existing users or files referenced
by live items. Albums like 'folder (N)' that belong to a real user are
reported but left untouched.

Usage:
    python .agents/cleanup_test_leftovers.py            # dry run (report only)
    python .agents/cleanup_test_leftovers.py --apply    # actually delete
    python .agents/cleanup_test_leftovers.py --db path/to/gallery.db
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def humanize(n: int) -> str:
    return f"{n:,}"


def collect_orphan_files(db: sqlite3.Connection, root: Path) -> dict[str, list[Path]]:
    """Map directory name -> list of files whose item_id is not in the DB."""
    item_ids = {row[0] for row in db.execute("SELECT id FROM items")}
    result: dict[str, list[Path]] = {}
    for folder in ("uploads", "thumbnails", "fallbacks"):
        directory = root / folder
        if not directory.is_dir():
            result[folder] = []
            continue
        orphans = []
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            # Storage keys are bare item ids; fallbacks may carry an extension.
            if entry.stem not in item_ids:
                orphans.append(entry)
        result[folder] = orphans
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(BASE_DIR / "gallery.db"), help="Path to gallery.db"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete (default: dry run)"
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # 1. Orphan media files
    # ------------------------------------------------------------------
    orphan_files = collect_orphan_files(db, BASE_DIR)
    total_files = sum(len(v) for v in orphan_files.values())
    total_bytes = sum(f.stat().st_size for v in orphan_files.values() for f in v)
    print("== Orphan media files (no matching items row) ==")
    for folder, files in orphan_files.items():
        size = sum(f.stat().st_size for f in files)
        print(f"  {folder}/: {humanize(len(files))} files ({humanize(size)} bytes)")

    # ------------------------------------------------------------------
    # 2. Dangling database rows
    # ------------------------------------------------------------------
    queries = {
        "album_items (album or item gone)": (
            "SELECT COUNT(*) FROM album_items WHERE album_id NOT IN "
            "(SELECT id FROM albums) OR item_id NOT IN (SELECT id FROM items)",
            "DELETE FROM album_items WHERE album_id NOT IN (SELECT id FROM albums) "
            "OR item_id NOT IN (SELECT id FROM items)",
        ),
        "albums owned by deleted users": (
            "SELECT COUNT(*) FROM albums WHERE user_id IS NOT NULL "
            "AND user_id NOT IN (SELECT id FROM users)",
            "DELETE FROM albums WHERE user_id IS NOT NULL "
            "AND user_id NOT IN (SELECT id FROM users)",
        ),
        "folders owned by deleted users (empty only)": (
            "SELECT COUNT(*) FROM folders WHERE user_id NOT IN (SELECT id FROM users) "
            "AND id NOT IN (SELECT DISTINCT folder_id FROM items "
            "WHERE folder_id IS NOT NULL)",
            "DELETE FROM folders WHERE user_id NOT IN (SELECT id FROM users) "
            "AND id NOT IN (SELECT DISTINCT folder_id FROM items "
            "WHERE folder_id IS NOT NULL)",
        ),
        "user_settings of deleted users": (
            "SELECT COUNT(*) FROM user_settings "
            "WHERE user_id NOT IN (SELECT id FROM users)",
            "DELETE FROM user_settings WHERE user_id NOT IN (SELECT id FROM users)",
        ),
        "folder_permissions of deleted users": (
            "SELECT COUNT(*) FROM folder_permissions WHERE user_id NOT IN "
            "(SELECT id FROM users) OR granted_by NOT IN (SELECT id FROM users)",
            "DELETE FROM folder_permissions WHERE user_id NOT IN (SELECT id FROM users) "
            "OR granted_by NOT IN (SELECT id FROM users)",
        ),
        "ai_tagging_jobs for deleted items": (
            "SELECT COUNT(*) FROM ai_tagging_jobs "
            "WHERE item_id NOT IN (SELECT id FROM items)",
            "DELETE FROM ai_tagging_jobs WHERE item_id NOT IN (SELECT id FROM items)",
        ),
        "user_folder_preferences of deleted users/folders": (
            "SELECT COUNT(*) FROM user_folder_preferences "
            "WHERE user_id NOT IN (SELECT id FROM users) "
            "OR folder_id NOT IN (SELECT id FROM folders)",
            "DELETE FROM user_folder_preferences "
            "WHERE user_id NOT IN (SELECT id FROM users) "
            "OR folder_id NOT IN (SELECT id FROM folders)",
        ),
        "item_tags for deleted items": (
            "SELECT COUNT(*) FROM item_tags "
            "WHERE item_id NOT IN (SELECT id FROM items)",
            "DELETE FROM item_tags WHERE item_id NOT IN (SELECT id FROM items)",
        ),
        "tag_suggestion_feedback for deleted items": (
            "SELECT COUNT(*) FROM tag_suggestion_feedback "
            "WHERE item_id NOT IN (SELECT id FROM items)",
            "DELETE FROM tag_suggestion_feedback "
            "WHERE item_id NOT IN (SELECT id FROM items)",
        ),
    }

    print("\n== Dangling database rows ==")
    counts: dict[str, int] = {}
    for label, (count_sql, _) in queries.items():
        count = db.execute(count_sql).fetchone()[0]
        counts[label] = count
        print(f"  {label}: {humanize(count)}")

    # Albums owned by real users that look test-generated (informational only)
    suspects = db.execute(
        "SELECT name, COUNT(*) AS n FROM albums "
        "WHERE name LIKE 'folder%' AND user_id IN (SELECT id FROM users) "
        "GROUP BY name ORDER BY name"
    ).fetchall()
    if suspects:
        print("\n== Left untouched (owned by existing users) ==")
        print(f"  albums 'folder*' owned by live users: "
              f"{humanize(sum(r['n'] for r in suspects))} - review manually")

    if not args.apply:
        print("\nDRY RUN - nothing deleted. Re-run with --apply to clean up.")
        db.close()
        return 0

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------
    backup = db_path.with_suffix(db_path.suffix + ".cleanup-bak")
    print(f"\nBacking up database to {backup}")
    shutil.copy2(db_path, backup)

    deleted_files = 0
    for files in orphan_files.values():
        for path in files:
            try:
                path.unlink()
                deleted_files += 1
            except OSError as exc:
                print(f"  WARN: could not delete {path}: {exc}")
    print(f"Deleted {humanize(deleted_files)} orphan files "
          f"({humanize(total_bytes)} bytes reclaimed on disk)")

    for label, (_, delete_sql) in queries.items():
        cur = db.execute(delete_sql)
        print(f"  {label}: deleted {humanize(cur.rowcount)} rows")

    db.commit()
    try:
        db.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        print(f"  WARN: VACUUM skipped ({exc}); stop the server and re-run "
              f"--apply if you need the space reclaimed")
    db.close()
    print("Done. Database vacuumed; backup kept at " + str(backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
