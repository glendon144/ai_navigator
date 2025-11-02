# weave_store.py
from __future__ import annotations
import json, sqlite3, time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, List, Dict, Any

DEFAULT_DB = Path("storage/ai_navigator.db")

@dataclass
class WeaveItem:
    id: int
    title: str
    url: str
    captured_at: int
    summary: str
    tags: str
    data_json: str

class WeaveStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._conn() as cx:
            cx.execute("""
            CREATE TABLE IF NOT EXISTS weaves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                captured_at INTEGER NOT NULL,
                summary TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                data_json TEXT DEFAULT '{}'
            )
            """)
            cx.execute("CREATE INDEX IF NOT EXISTS idx_weaves_time ON weaves(captured_at DESC)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_weaves_url ON weaves(url)")

    def capture(self, *, title: str, url: str, summary: str = "", tags: Iterable[str] = (), data: Optional[Dict[str, Any]] = None) -> int:
        ts = int(time.time())
        tags_str = ",".join(t.strip() for t in tags if t and t.strip())
        data_json = json.dumps(data or {}, ensure_ascii=False)
        with self._conn() as cx:
            cur = cx.execute(
                "INSERT INTO weaves (title, url, captured_at, summary, tags, data_json) VALUES (?,?,?,?,?,?)",
                (title, url, ts, summary, tags_str, data_json),
            )
            return cur.lastrowid

    def list_recent(self, limit: int = 200) -> List[WeaveItem]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT id,title,url,captured_at,summary,tags,data_json FROM weaves ORDER BY captured_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [WeaveItem(*r) for r in rows]

    def search(self, query: str, limit: int = 200) -> List[WeaveItem]:
        like = f"%{query}%"
        with self._conn() as cx:
            rows = cx.execute(
                """SELECT id,title,url,captured_at,summary,tags,data_json
                   FROM weaves
                   WHERE title LIKE ? OR url LIKE ? OR summary LIKE ? OR tags LIKE ?
                   ORDER BY captured_at DESC LIMIT ?""",
                (like, like, like, like, limit),
            ).fetchall()
        return [WeaveItem(*r) for r in rows]

    def get(self, weave_id: int) -> Optional[WeaveItem]:
        with self._conn() as cx:
            r = cx.execute(
                "SELECT id,title,url,captured_at,summary,tags,data_json FROM weaves WHERE id=?",
                (weave_id,),
            ).fetchone()
        return WeaveItem(*r) if r else None

