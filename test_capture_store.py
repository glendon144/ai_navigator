from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

from capture_store import build_handoff, ensure_capture_columns, save_capture


def _database(tmp_path: Path) -> Path:
    db = tmp_path / "captures.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE archive_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT,
                captured_at TEXT, snippet TEXT, html TEXT, clean_html TEXT
            )
        """)
    return db


def test_migration_is_idempotent(tmp_path):
    db = _database(tmp_path)
    ensure_capture_columns(db)
    ensure_capture_columns(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(archive_pages)")}
    assert {"capture_type", "selection_text", "summary", "tags", "note"} <= columns


def test_save_selection_capture_and_build_handoffs(tmp_path):
    db = _database(tmp_path)
    capture = save_capture(
        db,
        url="https://example.com/article",
        title="Useful Article",
        page_html="<p>Ignored full page</p>",
        selection_text="A selected idea worth remembering.",
    )
    assert capture.capture_type == "selection"
    assert capture.selection_text == "A selected idea worth remembering."
    assert "example.com" in capture.tags
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT capture_type, selection_text FROM archive_pages WHERE id = ?",
            (capture.id,),
        ).fetchone()
    assert row == ("selection", "A selected idea worth remembering.")
    assert "propose an OPML location" in build_handoff(capture, "PiKit")
    assert "useful next questions" in build_handoff(capture, "FunKit")
