"""Local Dream Capture persistence and handoff helpers.

The suite does not yet ship the networked Dream Capsule substrate.  These
helpers deliberately extend AI Navigator's existing archive database so the
UI and data model can migrate to DCCP later without losing captures.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class DreamCapture:
    id: int
    url: str
    title: str
    captured_at: str
    capture_type: str
    selection_text: str
    summary: str
    tags: str
    note: str


def ensure_capture_columns(db_path: Path) -> None:
    """Add capture metadata to older archive databases in-place."""
    columns = {
        "capture_type": "TEXT NOT NULL DEFAULT 'page'",
        "selection_text": "TEXT NOT NULL DEFAULT ''",
        "summary": "TEXT NOT NULL DEFAULT ''",
        "tags": "TEXT NOT NULL DEFAULT ''",
        "note": "TEXT NOT NULL DEFAULT ''",
    }
    with sqlite3.connect(db_path) as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(archive_pages)")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE archive_pages ADD COLUMN {name} {declaration}")


def suggest_summary(text: str, max_chars: int = 420) -> str:
    """Return a compact, deterministic extractive summary for offline use."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    chosen: list[str] = []
    used = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if chosen else 0)
        if chosen and used + extra > max_chars:
            break
        chosen.append(sentence)
        used += extra
        if used >= max_chars * 0.7:
            break
    summary = " ".join(chosen).strip()
    if not summary or len(summary) > max_chars:
        summary = cleaned[: max_chars - 1].rstrip() + "…"
    return summary


def suggest_tags(url: str, title: str, text: str, limit: int = 5) -> str:
    """Suggest useful, transparent tags without requiring an AI provider."""
    stopwords = {
        "about", "after", "again", "also", "been", "being", "from", "have",
        "into", "more", "most", "other", "that", "their", "there", "these",
        "they", "this", "those", "were", "what", "when", "where", "which",
        "with", "would", "your", "https", "www",
    }
    candidates: list[str] = []
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if host:
        candidates.append(host)
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", f"{title} {text[:1200]}")
    counts: dict[str, int] = {}
    for word in words:
        key = word.lower()
        if key not in stopwords:
            counts[key] = counts.get(key, 0) + 1
    candidates.extend(sorted(counts, key=lambda word: (-counts[word], word)))
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) == limit:
            break
    return ", ".join(unique)


def save_capture(
    db_path: Path,
    *,
    url: str,
    title: str,
    page_html: str,
    selection_text: str = "",
    note: str = "",
) -> DreamCapture:
    """Persist a page or selection capture in the existing archive database."""
    ensure_capture_columns(db_path)
    selection = re.sub(r"\s+", " ", selection_text or "").strip()
    capture_type = "selection" if selection else "page"
    source_text = selection or re.sub(r"<[^>]+>", " ", page_html or "")
    source_text = re.sub(r"\s+", " ", source_text).strip()
    summary = suggest_summary(source_text)
    tags = suggest_tags(url, title, source_text)
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    snippet = summary[:500]
    stored_html = (
        f"<article><h1>{escape(title)}</h1><blockquote>{escape(selection)}</blockquote>"
        f"<p><a href=\"{escape(url, quote=True)}\">Source</a></p></article>"
        if selection
        else page_html
    )
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO archive_pages
                (url, title, captured_at, snippet, html, clean_html,
                 capture_type, selection_text, summary, tags, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, title, captured_at, snippet, stored_html, stored_html,
             capture_type, selection, summary, tags, note.strip()),
        )
        capture_id = int(cursor.lastrowid)
    return DreamCapture(capture_id, url, title, captured_at, capture_type,
                        selection, summary, tags, note.strip())


def build_handoff(capture: DreamCapture, destination: str) -> str:
    """Build a clipboard packet that PiKit or FunKit can consume today."""
    if destination == "PiKit":
        instruction = "Organize this source in PiKit. Preserve the URL and propose an OPML location."
    else:
        instruction = "Use this Dream Capture as context. Identify the key ideas and useful next questions."
    body = capture.selection_text or capture.summary
    return (
        f"# Dream Capture\n\nTitle: {capture.title}\nSource: {capture.url}\n"
        f"Captured: {capture.captured_at}\nTags: {capture.tags}\n\n"
        f"## Summary\n{capture.summary}\n\n## Captured content\n{body}\n\n"
        f"## Handoff\n{instruction}"
    )
