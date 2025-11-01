#!/usr/bin/env python3
# init_db.py
#
# Initializes the AI Navigator SQLite database and storage directory.
# Creates (or migrates) the archive_pages table used by ai_navigator.py.

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


# Default locations (keep in sync with ai_navigator.py)
STORAGE_DIR = Path("storage")
DB_PATH = STORAGE_DIR / "search_time_machine.db"


def _ensure_storage_dir(path: Path) -> None:
    """
    Create the storage directory if it doesn't exist.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f"Could not create storage directory {path}: {e}") from e


def _connect(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite connection with pragmatic defaults for desktop apps.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")      # better concurrent reads
    conn.execute("PRAGMA synchronous = NORMAL;")    # faster, safe enough
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_archive_table(conn: sqlite3.Connection) -> None:
    """
    Create (or migrate) the archive_pages table.
    Columns:
      id           INTEGER PRIMARY KEY AUTOINCREMENT
      url          TEXT
      title        TEXT
      captured_at  TEXT         -- ISO8601 UTC (e.g., 2025-10-31T04:54:35Z)
      snippet      TEXT
      html         TEXT         -- raw HTML
      clean_html   TEXT         -- Reader-Mode sanitized HTML
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT,
            captured_at TEXT,
            snippet TEXT,
            html TEXT,
            clean_html TEXT
        );
        """
    )
    # Migration guard: add clean_html if an older DB lacks it.
    try:
        cur.execute("ALTER TABLE archive_pages ADD COLUMN clean_html TEXT;")
    except sqlite3.OperationalError:
        # Column already exists; ignore.
        pass

    # Helpful indices for listing and domain/URL queries.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_pages_captured_at "
        "ON archive_pages (captured_at DESC);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_pages_url "
        "ON archive_pages (url);"
    )

    conn.commit()


def init_db_if_needed(db_path: Optional[Path | str] = None) -> None:
    """
    Public entry point. Ensures storage/ exists and the DB is initialized.

    Usage:
        from init_db import init_db_if_needed
        init_db_if_needed()  # uses storage/search_time_machine.db

        # or, to specify a custom path:
        init_db_if_needed('/path/to/my.db')
    """
    target = Path(db_path) if db_path else DB_PATH
    _ensure_storage_dir(target.parent)
    conn = _connect(target)
    try:
        _ensure_archive_table(conn)
    finally:
        conn.close()


# Optional: allow running this module directly for a quick sanity check.
if __name__ == "__main__":
    init_db_if_needed()
    print(f"Initialized: {DB_PATH.resolve()}")

