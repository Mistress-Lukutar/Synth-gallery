import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "gallery.db"

_connection = None


def get_db() -> sqlite3.Connection:
    """Get database connection"""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection


def init_db():
    """Initialize database schema"""
    db = get_db()

    # Albums table
    db.execute("""
        CREATE TABLE IF NOT EXISTS albums (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ai_processed INTEGER DEFAULT 0,
            album_id TEXT,
            position INTEGER DEFAULT 0,
            media_type TEXT DEFAULT 'image',
            FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
        )
    """)

    # Tag categories with colors
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL
        )
    """)

    # Preset tags library
    db.execute("""
        CREATE TABLE IF NOT EXISTS tag_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id),
            UNIQUE(name, category_id)
        )
    """)

    # Photo tags with category reference
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            category_id INTEGER,
            confidence REAL DEFAULT 1.0,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES tag_categories(id)
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_photo_id ON tags(photo_id)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tag_presets_category ON tag_presets(category_id)
    """)

    # Migration: add category_id to existing tags table if missing
    columns = [row[1] for row in db.execute("PRAGMA table_info(tags)").fetchall()]
    if "category_id" not in columns:
        db.execute("ALTER TABLE tags ADD COLUMN category_id INTEGER")

    # Migration: add album_id, position, and media_type to existing photos table if missing
    photo_columns = [row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()]
    if "album_id" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN album_id TEXT")
    if "position" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN position INTEGER DEFAULT 0")
    if "media_type" not in photo_columns:
        db.execute("ALTER TABLE photos ADD COLUMN media_type TEXT DEFAULT 'image'")

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_album_id ON photos(album_id)
    """)

    # Insert default categories if not exist
    default_categories = [
        ("Subject", "#3b82f6"),      # Blue - people, animals, objects
        ("Location", "#22c55e"),     # Green - places, settings
        ("Mood", "#f59e0b"),         # Amber - emotions, atmosphere
        ("Style", "#a855f7"),        # Purple - artistic style, technique
        ("Event", "#ef4444"),        # Red - occasions, activities
        ("Other", "#6b7280"),        # Gray - miscellaneous
    ]

    for name, color in default_categories:
        db.execute(
            "INSERT OR IGNORE INTO tag_categories (name, color) VALUES (?, ?)",
            (name, color)
        )

    # Insert some default preset tags
    default_presets = [
        # Subject
        ("person", "Subject"), ("people", "Subject"), ("portrait", "Subject"),
        ("animal", "Subject"), ("dog", "Subject"), ("cat", "Subject"),
        ("bird", "Subject"), ("flower", "Subject"), ("tree", "Subject"),
        ("car", "Subject"), ("building", "Subject"), ("food", "Subject"),
        # Location
        ("outdoor", "Location"), ("indoor", "Location"), ("city", "Location"),
        ("nature", "Location"), ("beach", "Location"), ("mountain", "Location"),
        ("forest", "Location"), ("park", "Location"), ("home", "Location"),
        ("street", "Location"), ("studio", "Location"),
        # Mood
        ("happy", "Mood"), ("calm", "Mood"), ("dramatic", "Mood"),
        ("romantic", "Mood"), ("mysterious", "Mood"), ("energetic", "Mood"),
        ("peaceful", "Mood"), ("melancholic", "Mood"),
        # Style
        ("black and white", "Style"), ("vintage", "Style"), ("minimalist", "Style"),
        ("abstract", "Style"), ("macro", "Style"), ("panorama", "Style"),
        ("long exposure", "Style"), ("bokeh", "Style"),
        # Event
        ("wedding", "Event"), ("birthday", "Event"), ("travel", "Event"),
        ("vacation", "Event"), ("concert", "Event"), ("sports", "Event"),
        ("family", "Event"), ("party", "Event"),
    ]

    for tag_name, category_name in default_presets:
        db.execute("""
            INSERT OR IGNORE INTO tag_presets (name, category_id)
            SELECT ?, id FROM tag_categories WHERE name = ?
        """, (tag_name, category_name))

    db.commit()
