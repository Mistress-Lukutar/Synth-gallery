"""Background scheduler for periodic tag stats rebuild."""
import threading
from datetime import datetime, timedelta
from typing import Optional

from ...database import create_connection
from ...config import TAG_STATS_SCHEDULE, TAG_STATS_HOUR
from ..repositories import TagCooccurrenceRepository, TagMutexRepository


class TagStatsScheduler:
    """Background scheduler for automatic tag stats rebuild."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_run: Optional[datetime] = None

    def start(self):
        """Start the tag stats scheduler thread."""
        if TAG_STATS_SCHEDULE == "disabled":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the tag stats scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            try:
                if self._should_run():
                    self._rebuild()
                    self._last_run = datetime.now()
            except Exception as e:
                print(f"[tag-stats-scheduler] Error: {e}")
            # Sleep in chunks to allow quick shutdown
            self._stop_event.wait(3600)  # Check every hour

    def _should_run(self) -> bool:
        """Determine if a rebuild should occur now."""
        now = datetime.now()
        if now.hour != TAG_STATS_HOUR:
            return False
        if self._last_run is None:
            return True
        if TAG_STATS_SCHEDULE == "daily":
            return (now - self._last_run) >= timedelta(hours=23)
        if TAG_STATS_SCHEDULE == "weekly":
            return (now - self._last_run) >= timedelta(days=6)
        return False

    def _rebuild(self):
        """Perform full stats rebuild."""
        print("[tag-stats-scheduler] Starting stats rebuild...")
        db = create_connection()
        try:
            cooccurrence = TagCooccurrenceRepository(db)
            mutex = TagMutexRepository(db)
            cooccurrence.rebuild_all_from_item_tags()
            mutex.rebuild_all()
            print("[tag-stats-scheduler] Stats rebuild complete.")
        finally:
            db.close()

    @property
    def status(self) -> dict:
        """Get scheduler status."""
        return {
            "enabled": TAG_STATS_SCHEDULE != "disabled",
            "schedule": TAG_STATS_SCHEDULE,
            "hour": TAG_STATS_HOUR,
            "running": self._thread.is_alive() if self._thread else False,
            "last_run": self._last_run.isoformat() if self._last_run else None,
        }


# Global scheduler instance
tag_stats_scheduler = TagStatsScheduler()
