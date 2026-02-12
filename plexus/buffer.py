"""
Pluggable buffer backends for local point storage.

Two implementations:
  - MemoryBuffer: In-memory list (default, matches original behavior)
  - SqliteBuffer: WAL-mode SQLite for persistence across restarts
"""

import json
import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BufferBackend(ABC):
    """Abstract buffer backend for storing telemetry points locally."""

    @abstractmethod
    def add(self, points: List[Dict[str, Any]]) -> None:
        """Add points to the buffer, evicting oldest if over capacity."""

    @abstractmethod
    def get_all(self) -> List[Dict[str, Any]]:
        """Return a copy of all buffered points without clearing."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all buffered points."""

    @abstractmethod
    def size(self) -> int:
        """Return current number of buffered points."""


class MemoryBuffer(BufferBackend):
    """In-memory buffer with FIFO eviction. Thread-safe.

    This extracts the original behavior from Plexus client._failed_buffer.
    """

    def __init__(self, max_size: int = 10_000):
        self._max_size = max_size
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, points: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._buffer.extend(points)
            if len(self._buffer) > self._max_size:
                overflow = len(self._buffer) - self._max_size
                logger.warning("Buffer full, dropped %d oldest points", overflow)
                self._buffer = self._buffer[overflow:]

    def get_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)


class SqliteBuffer(BufferBackend):
    """SQLite-backed persistent buffer using WAL mode. Thread-safe.

    Survives process restarts. Points are stored as JSON blobs in a single
    table with auto-incrementing rowid for FIFO ordering.

    Args:
        path: Path to the SQLite database file.
              Defaults to ~/.plexus/buffer.db
        max_size: Maximum number of points to retain. Default 100,000.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        max_size: int = 100_000,
    ):
        self._max_size = max_size
        self._lock = threading.Lock()

        if path is None:
            plexus_dir = os.path.join(os.path.expanduser("~"), ".plexus")
            os.makedirs(plexus_dir, exist_ok=True)
            path = os.path.join(plexus_dir, "buffer.db")

        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS points ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  data TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def add(self, points: List[Dict[str, Any]]) -> None:
        if not points:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO points (data) VALUES (?)",
                [(json.dumps(p),) for p in points],
            )
            self._conn.commit()
            self._evict()

    def get_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute("SELECT data FROM points ORDER BY id")
            return [json.loads(row[0]) for row in cursor.fetchall()]

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM points")
            self._conn.commit()

    def size(self) -> int:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM points")
            return cursor.fetchone()[0]

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()

    def _evict(self) -> None:
        """Remove oldest rows if over max_size. Must be called with lock held."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM points")
        count = cursor.fetchone()[0]
        if count > self._max_size:
            overflow = count - self._max_size
            self._conn.execute(
                "DELETE FROM points WHERE id IN ("
                "  SELECT id FROM points ORDER BY id LIMIT ?"
                ")",
                (overflow,),
            )
            self._conn.commit()
            logger.warning("Buffer full, dropped %d oldest points", overflow)
