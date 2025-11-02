#!/usr/bin/env python3
"""
navigator_rpc.py — JSON-RPC 2.0 service for AI Navigator

HTTP endpoint:
  POST /rpc
  Payload: {"jsonrpc":"2.0","method":"<name>","params":{...},"id":1}

Methods:
  - ping()
  - version()
  - info()
  - list_snapshots(limit=100, offset=0, query=None)
  - get_snapshot(id)
  - get_snapshot_html(id, reader_mode=True)
  - context_capsule(id, hard_cap_chars=6500)
  - memory_weave(id=None, k=3, hard_cap_chars=7000)
  - export_opml(owner_name="Glen", out_path="archive_export.opml")
  - archive_raw(url, title, html)
  - archive_fetch(url, title=None, timeout=20)   # optional: requires 'requests'

Run:
  python navigator_rpc.py --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

try:
    # aopmlengine should be co-located with ai_navigator.py
    import aopmlengine
except Exception:
    aopmlengine = None  # OPML export will error nicely if missing

try:
    import requests  # optional for archive_fetch
except Exception:
    requests = None

from flask import Flask, request, jsonify

# Keep in sync with ai_navigator.py
DB_PATH = Path("storage") / "search_time_machine.db"
DEFAULT_OPML_PATH = "archive_export.opml"


# -------------------------- DB & HTML helpers --------------------------


def ensure_archive_table(db_path: Path):
    conn = sqlite3.connect(db_path)
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
    # tolerate existing clean_html
    try:
        cur.execute("ALTER TABLE archive_pages ADD COLUMN clean_html TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def html_to_snippet(html: str, max_len: int = 500) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text[:max_len]


def sanitize_html_for_reader(raw_html: str) -> str:
    cleaned = re.sub(
        r"<script.*?</script>", "", raw_html, flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = re.sub(
        r"<iframe.*?</iframe>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = re.sub(
        r"<link[^>]+rel=['\"]?(preload|dns-prefetch|preconnect|modulepreload)['\"]?[^>]*>",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\son\w+\s*=\s*['\"].*?['\"]", "", cleaned, flags=re.IGNORECASE | re.DOTALL
    )
    return cleaned


def save_archive_page(db_path: Path, url: str, title: str, html: str) -> int:
    ensure_archive_table(db_path)
    captured_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    snippet = html_to_snippet(html)
    clean_html = sanitize_html_for_reader(html)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO archive_pages (url, title, captured_at, snippet, html, clean_html)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (url, title, captured_at, snippet, html, clean_html),
    )
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(rowid)


def _clean_for_capsule(s: str) -> str:
    s = (s or "").replace("```", "ʼʼʼ")
    s = re.sub(r"\s+\n", "\n", s)
    return s.strip()


def build_context_capsule_for_snapshot(
    *,
    title: str,
    url: str,
    captured_at: str,
    snippet: str,
    body: str,
    hard_cap_chars: int = 6500,
) -> str:
    title = _clean_for_capsule(title)
    url = _clean_for_capsule(url)
    snippet = _clean_for_capsule(snippet)
    body = _clean_for_capsule(body)

    max_body = max(0, min(5200, hard_cap_chars - 1000))
    body_slice = body[:max_body]

    header = (
        f"### Context Capsule — ai_navigator\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Captured: {captured_at}\n"
        f"---\n"
    )
    snippet_block = f"**Snippet**\n{snippet}\n\n" if snippet else ""
    html_block = f"**Reader-Mode HTML (excerpt)**\n```html\n{body_slice}\n```\n"
    footer = (
        "\nContinue from this capsule. Summarize key points from the page, "
        "then propose the next 1–2 actions or questions. If anything is unclear, "
        "ask for the single most relevant detail rather than restarting."
    )

    capsule = header + snippet_block + html_block + footer
    if len(capsule) > hard_cap_chars:
        capsule = capsule[: hard_cap_chars - 25] + "\n…[truncated]…"
    return capsule


def build_memory_weave_packet(
    conn: sqlite3.Connection,
    current_page_id: Optional[int],
    k: int = 3,
    hard_cap_chars: int = 7000,
) -> str:
    cur = conn.cursor()

    sel_url = None
    if current_page_id is not None:
        cur.execute(
            "SELECT url FROM archive_pages WHERE id = ?;",
            (current_page_id,),
        )
        row = cur.fetchone()
        if row:
            sel_url = (row[0] or "").strip()

    from urllib.parse import urlparse

    domain = (urlparse(sel_url).netloc.lower() if sel_url else "") if sel_url else ""

    items: List[Tuple[int, str, str, str, str]] = []
    if domain:
        cur.execute(
            """
            SELECT id, title, url, captured_at, snippet
            FROM archive_pages
            WHERE url LIKE ?
            ORDER BY captured_at DESC
            LIMIT ?;
            """,
            (f"%://{domain}%", k),
        )
        items = cur.fetchall()

    if len(items) < k:
        have_ids = {r[0] for r in items}
        need = k - len(items)
        cur.execute(
            """
            SELECT id, title, url, captured_at, snippet
            FROM archive_pages
            ORDER BY captured_at DESC
            LIMIT ?;
            """,
            (k * 3,),
        )
        for r in cur.fetchall():
            if r[0] not in have_ids:
                items.append(r)
                if len(items) >= k:
                    break

    header = "### Context Capsule — ai_navigator\n"
    if domain:
        header += f"Thread scope: {domain}\n"
    else:
        header += "Thread scope: global\n"
    header += f"Captured: {datetime.utcnow().isoformat(timespec='seconds')}Z\n---\n"

    lines = []
    for _id, title, url, ts, snip in items:
        title = _clean_for_capsule(title or "(untitled)")
        url = _clean_for_capsule(url or "")
        ts = _clean_for_capsule(ts or "")
        snip = _clean_for_capsule((snip or "")[:240])
        lines.append(f"— {ts} · {title} · {url}")
        if snip:
            lines.append(f"   {snip}")

    footer = (
        "\n(End of memory weave)\n\n"
        "Continue from these three context points. Summarize the through-line you infer, "
        "then propose the next one or two actions."
    )

    capsule = header + "\n".join(lines) + "\n" + footer
    if len(capsule) > hard_cap_chars:
        capsule = capsule[: hard_cap_chars - 25] + "\n…[truncated]…"
    return capsule


# -------------------------- JSON-RPC plumbing --------------------------


class RPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class NavigatorRPC:
    def __init__(self, db_path: Path = DB_PATH, opml_path: str = DEFAULT_OPML_PATH):
        self.db_path = db_path
        self.opml_path = opml_path
        ensure_archive_table(self.db_path)

    # --- Methods ---

    def ping(self):
        return "pong"

    def version(self):
        return {
            "rpc": "1.0",
            "service": "navigator_rpc",
            "caps": ["archive", "opml", "capsule", "weave"],
        }

    def info(self):
        return {
            "db_path": str(self.db_path),
            "opml_path": self.opml_path,
            "has_requests": bool(requests),
            "has_aopmlengine": bool(aopmlengine),
        }

    def list_snapshots(
        self, limit: int = 100, offset: int = 0, query: Optional[str] = None
    ):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        if query:
            like = f"%{query}%"
            cur.execute(
                """
                SELECT id, title, url, captured_at, snippet
                FROM archive_pages
                WHERE title LIKE ? OR url LIKE ? OR snippet LIKE ?
                ORDER BY captured_at DESC
                LIMIT ? OFFSET ?;
                """,
                (like, like, like, int(limit), int(offset)),
            )
        else:
            cur.execute(
                """
                SELECT id, title, url, captured_at, snippet
                FROM archive_pages
                ORDER BY captured_at DESC
                LIMIT ? OFFSET ?;
                """,
                (int(limit), int(offset)),
            )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "title": r[1],
                "url": r[2],
                "captured_at": r[3],
                "snippet": r[4],
            }
            for r in rows
        ]

    def get_snapshot(self, id: int):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, url, captured_at, snippet FROM archive_pages WHERE id = ?;",
            (int(id),),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            raise RPCError(-32004, f"snapshot {id} not found")
        return {
            "id": row[0],
            "title": row[1],
            "url": row[2],
            "captured_at": row[3],
            "snippet": row[4],
        }

    def get_snapshot_html(self, id: int, reader_mode: bool = True):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        if reader_mode:
            cur.execute(
                "SELECT COALESCE(clean_html, html) FROM archive_pages WHERE id = ?;",
                (int(id),),
            )
        else:
            cur.execute("SELECT html FROM archive_pages WHERE id = ?;", (int(id),))
        row = cur.fetchone()
        conn.close()
        if not row or not row[0]:
            raise RPCError(-32004, f"snapshot {id} has no html")
        return {"html": row[0]}

    def context_capsule(self, id: int, hard_cap_chars: int = 6500):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT title, url, captured_at, snippet, COALESCE(clean_html, html)
            FROM archive_pages
            WHERE id = ?;
            """,
            (int(id),),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            raise RPCError(-32004, f"snapshot {id} not found")
        title, url, captured_at, snippet, body = row
        capsule = build_context_capsule_for_snapshot(
            title=title or url or "(untitled)",
            url=url or "about:blank",
            captured_at=captured_at or "",
            snippet=snippet or "",
            body=body or "",
            hard_cap_chars=int(hard_cap_chars),
        )
        return {"capsule": capsule}

    def memory_weave(
        self, id: Optional[int] = None, k: int = 3, hard_cap_chars: int = 7000
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            capsule = build_memory_weave_packet(
                conn,
                int(id) if id is not None else None,
                k=int(k),
                hard_cap_chars=int(hard_cap_chars),
            )
            return {"capsule": capsule}
        finally:
            conn.close()

    def export_opml(self, owner_name: str = "Glen", out_path: str = DEFAULT_OPML_PATH):
        if not aopmlengine:
            raise RPCError(-32010, "aopmlengine not available")
        xml = aopmlengine.export_archive_to_opml(
            db_path=str(self.db_path),
            out_path=str(out_path),
            owner_name=owner_name,
        )
        # aopmlengine writes to file; we also return the XML for convenience
        return {"out_path": out_path, "xml": xml}

    def archive_raw(self, url: str, title: str, html: str):
        rowid = save_archive_page(self.db_path, url=url, title=title, html=html)
        return {"id": rowid}

    def archive_fetch(self, url: str, title: Optional[str] = None, timeout: int = 20):
        if not requests:
            raise RPCError(-32020, "requests module not available")
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        title_guess = title or url
        rowid = save_archive_page(
            self.db_path, url=url, title=title_guess, html=resp.text
        )
        return {"id": rowid, "bytes": len(resp.text)}


# -------------------------- Flask app --------------------------


def make_app(service: NavigatorRPC) -> Flask:
    app = Flask(__name__)

    @app.post("/rpc")
    def rpc():
        try:
            payload = request.get_json(force=True, silent=False)
        except Exception:
            return (
                jsonify(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None,
                    }
                ),
                400,
            )

        def error(idval, code, message, data=None, http=200):
            body = {
                "jsonrpc": "2.0",
                "error": {"code": code, "message": message},
                "id": idval,
            }
            if data is not None:
                body["error"]["data"] = data
            return jsonify(body), http

        if not isinstance(payload, dict):
            return error(None, -32600, "Invalid Request", http=400)

        rpc_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params", {}) or {}

        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return error(rpc_id, -32600, "Invalid Request", http=400)

        try:
            fn = getattr(service, method)
        except AttributeError:
            return error(rpc_id, -32601, f"Method not found: {method}", http=404)

        try:
            if isinstance(params, dict):
                result = fn(**params)
            elif isinstance(params, list):
                result = fn(*params)
            else:
                return error(rpc_id, -32602, "Invalid params", http=400)
            return jsonify({"jsonrpc": "2.0", "result": result, "id": rpc_id})
        except RPCError as e:
            return error(rpc_id, e.code, e.message, data=e.data)
        except Exception as e:
            return error(rpc_id, -32000, "Server error", data=str(e))

    return app


def main():
    parser = argparse.ArgumentParser(description="AI Navigator JSON-RPC service")
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="bind port (default: 8765)"
    )
    args = parser.parse_args()

    os.makedirs(DB_PATH.parent, exist_ok=True)
    ensure_archive_table(DB_PATH)

    svc = NavigatorRPC(DB_PATH, DEFAULT_OPML_PATH)
    app = make_app(svc)
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
