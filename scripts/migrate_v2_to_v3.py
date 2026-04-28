"""Migrate tag system from v2 (materialized path) to v3 (flat + implications)."""
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "gallery.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("[1/7] Creating new tables...")

    # Tag implications
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tag_implications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            implies_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(tag_id, implies_tag_id)
        )
    """)

    # Co-occurrence
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tag_cooccurrence (
            tag_a_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            tag_b_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            count INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tag_a_id, tag_b_id),
            CHECK (tag_a_id < tag_b_id)
        )
    """)

    # Indexes for cooccurrence
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cooccurrence_count_a
        ON tag_cooccurrence(tag_a_id, count DESC)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cooccurrence_count_b
        ON tag_cooccurrence(tag_b_id, count DESC)
    """)

    print("[2/7] Altering item_tags...")
    # Check if is_explicit already exists
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(item_tags)")]
    if "is_explicit" not in cols:
        cursor.execute("ALTER TABLE item_tags ADD COLUMN is_explicit INTEGER NOT NULL DEFAULT 1")

    print("[3/7] Migrating parent_id to tag_implications...")
    cursor.execute("""
        INSERT OR IGNORE INTO tag_implications (tag_id, implies_tag_id)
        SELECT id, parent_id FROM tags WHERE parent_id IS NOT NULL
    """)
    implications_created = cursor.rowcount
    print(f"       Created {implications_created} implication edges from parent_id")

    print("[4/7] Building transitive closure and materializing implied tags...")

    # Get all items with their explicit tags
    item_tags_rows = cursor.execute("""
        SELECT item_id, tag_id FROM item_tags WHERE is_explicit = 1 OR is_explicit IS NULL
    """).fetchall()

    # Group by item
    from collections import defaultdict
    item_explicit = defaultdict(set)
    for row in item_tags_rows:
        item_explicit[row["item_id"]].add(row["tag_id"])

    # Build implication graph
    implications = cursor.execute("SELECT tag_id, implies_tag_id FROM tag_implications").fetchall()
    graph = defaultdict(set)
    for row in implications:
        graph[row["tag_id"]].add(row["implies_tag_id"])

    def get_closure(tag_ids: set) -> set:
        """Return all tag IDs reachable via implication graph."""
        result = set()
        stack = list(tag_ids)
        visited = set()
        while stack:
            tid = stack.pop()
            if tid in visited:
                continue
            visited.add(tid)
            for implied in graph.get(tid, set()):
                if implied not in visited:
                    result.add(implied)
                    stack.append(implied)
        return result

    # Clear existing implied tags (keep explicit)
    cursor.execute("DELETE FROM item_tags WHERE is_explicit = 0")

    # Re-insert with implied tags
    total_items = len(item_explicit)
    for idx, (item_id, explicit_ids) in enumerate(item_explicit.items(), 1):
        implied_ids = get_closure(explicit_ids)
        for tid in explicit_ids:
            cursor.execute("""
                INSERT OR IGNORE INTO item_tags (item_id, tag_id, is_explicit)
                VALUES (?, ?, 1)
            """, (item_id, tid))
        for tid in implied_ids:
            cursor.execute("""
                INSERT OR IGNORE INTO item_tags (item_id, tag_id, is_explicit)
                VALUES (?, ?, 0)
            """, (item_id, tid))
        if idx % 100 == 0:
            print(f"       Processed {idx}/{total_items} items...")

    print("[5/7] Rebuilding usage_count from materialized tags...")
    cursor.execute("""
        UPDATE tags SET usage_count = (
            SELECT COUNT(DISTINCT item_id) FROM item_tags WHERE tag_id = tags.id
        )
    """)

    print("[6/7] Building co-occurrence matrix...")
    # For each item, get all its tags, create pairs
    cursor.execute("SELECT item_id, tag_id FROM item_tags")
    item_all_tags = defaultdict(set)
    for row in cursor.fetchall():
        item_all_tags[row["item_id"]].add(row["tag_id"])

    from itertools import combinations
    pair_counts = defaultdict(int)
    for tags in item_all_tags.values():
        for a, b in combinations(sorted(tags), 2):
            pair_counts[(a, b)] += 1

    # Batch insert co-occurrence
    batch = []
    for (a, b), count in pair_counts.items():
        batch.append((a, b, count))
        if len(batch) >= 500:
            cursor.executemany("""
                INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE SET count = excluded.count
            """, batch)
            batch = []
    if batch:
        cursor.executemany("""
            INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count)
            VALUES (?, ?, ?)
            ON CONFLICT(tag_a_id, tag_b_id) DO UPDATE SET count = excluded.count
        """, batch)

    print("[7/7] Cleaning up old indexes...")
    cursor.execute("DROP INDEX IF EXISTS idx_tags_path")
    cursor.execute("DROP INDEX IF EXISTS idx_tags_parent")

    conn.commit()
    conn.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    migrate()
