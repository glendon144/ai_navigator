#!/usr/bin/env python3
"""
aopmlengine.py — OPML exporter for AI Navigator / FunKit

- Reads AI Navigator's SQLite archive (table: archive_pages)
- Emits a single OPML file (outline tree)
- Every string is XML 1.0–safe (control chars stripped; entities escaped)

Public API:
    export_archive_to_opml(db_path: str, out_path: str, owner_name: str|None) -> str
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from xml.sax.saxutils import escape as _xml_escape

try:
    # BeautifulSoup is available in your project; used to extract headings
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

log = logging.getLogger("aopmlengine")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)

# ---------------- XML safety ----------------

# XML 1.0 legal char ranges (minus discouraged chars)
# https://www.w3.org/TR/xml/#charsets
def _xml_strip_illegal(s: str) -> str:
    if not s:
        return ""
    out = []
    for ch in s:
        cp = ord(ch)
        if (
            cp == 0x9 or cp == 0xA or cp == 0xD or
            (0x20 <= cp <= 0xD7FF) or
            (0xE000 <= cp <= 0xFFFD) or
            (0x10000 <= cp <= 0x10FFFF)
        ):
            out.append(ch)
        # else drop it silently
    return "".join(out)

def _xml(s: str) -> str:
    return _xml_escape(_xml_strip_illegal(str(s or "")))

# ---------------- Minimal OPML model ----------------

@dataclass
class Outline:
    text: str
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List["Outline"] = field(default_factory=list)

    def add(self, child: "Outline") -> None:
        self.children.append(child)

    def to_xml(self, indent: int = 2, level: int = 0) -> str:
        pad = " " * (indent * level)
        # ensure attr safety
        attrs = {"text": _xml(self.text)}
        for k, v in self.attrs.items():
            attrs[k] = _xml(v)
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())

        if not self.children:
            return f"{pad}<outline {attr_str}/>\n"

        buf = io.StringIO()
        buf.write(f"{pad}<outline {attr_str}>\n")
        for c in self.children:
            buf.write(c.to_xml(indent=indent, level=level + 1))
        buf.write(f"{pad}</outline>\n")
        return buf.getvalue()

@dataclass
class OPMLDocument:
    title: str
    owner_name: Optional[str] = None
    date_created: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    outlines: List[Outline] = field(default_factory=list)

    def add(self, o: Outline) -> None:
        self.outlines.append(o)

    def to_xml(self, indent: int = 2) -> str:
        head = io.StringIO()
        head.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        head.write("<opml version=\"2.0\">\n")
        head.write("  <head>\n")
        head.write(f"    <title>{_xml(self.title)}</title>\n")
        head.write(f"    <dateCreated>{_xml(self.date_created)}</dateCreated>\n")
        head.write("    <generator>AI Navigator OPML Engine</generator>\n")
        if self.owner_name:
            head.write(f"    <ownerName>{_xml(self.owner_name)}</ownerName>\n")
        head.write("  </head>\n")
        head.write("  <body>\n")

        body = io.StringIO()
        for o in self.outlines:
            body.write(o.to_xml(indent=indent, level=1))

        tail = "  </body>\n</opml>\n"
        return head.getvalue() + body.getvalue() + tail

# ---------------- HTML → outline helpers ----------------

_HLEVEL = re.compile(r"^h([1-6])$", re.I)

def _headings_from_html(html: str) -> List[tuple[int, str]]:
    """Return list of (level, text) for h1..h6 in order."""
    if not html or not BeautifulSoup:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: List[tuple[int, str]] = []
    for tag in soup.find_all(re.compile(r"h[1-6]", re.I)):
        name = tag.name or ""
        m = _HLEVEL.match(name)
        if not m:
            continue
        lvl = int(m.group(1))
        text = tag.get_text(" ", strip=True)
        if text:
            out.append((lvl, text))
    return out

def _attach_headings(parent: Outline, html: str) -> None:
    nodes = _headings_from_html(html)
    if not nodes:
        return
    # Simple stack-based nesting
    stack: List[tuple[int, Outline]] = []
    for level, text in nodes:
        node = Outline(text=text)
        while stack and level <= stack[-1][0]:
            stack.pop()
        if not stack:
            parent.add(node)
        else:
            stack[-1][1].add(node)
        stack.append((level, node))

# ---------------- DB → OPML ----------------

def export_archive_to_opml(
    db_path: str = "storage/search_time_machine.db",
    out_path: str = "archive_export.opml",
    owner_name: Optional[str] = None,
) -> str:
    """
    Build an OPML from AI Navigator's archive_pages table.

    Table schema expected:
      id INTEGER PRIMARY KEY
      url TEXT
      title TEXT
      captured_at TEXT
      snippet TEXT
      html TEXT
      clean_html TEXT
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")

    doc = OPMLDocument(title="AI Navigator Archive", owner_name=owner_name)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, url, COALESCE(title, ''), COALESCE(captured_at, ''), COALESCE(snippet, ''),
               COALESCE(clean_html, html, '')
        FROM archive_pages
        ORDER BY captured_at DESC, id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        pid, url, title, captured_at, snippet, body = row
        title = title or url or "(untitled)"

        top = Outline(
            text=title,
            attrs={
                "url": url or "",
                "captured_at": captured_at or "",
                "_local_id": str(pid),
            },
        )

        if snippet:
            top.add(Outline(text=f"Snippet: {snippet[:200]}"))

        # Add a heading subtree if any
        if body:
            _attach_headings(top, body)

        doc.add(top)

    xml = doc.to_xml()
    # Write atomically
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(tmp, out_path)
    log.info("Wrote OPML: %s", out_path)
    return xml

# ---------------- CLI (optional) ----------------

def _parse_argv(argv: List[str]) -> dict:
    import argparse
    p = argparse.ArgumentParser(description="Export AI Navigator archive to OPML")
    p.add_argument("--db", default="storage/search_time_machine.db", help="Path to SQLite DB")
    p.add_argument("--out", default="archive_export.opml", help="Path to write OPML")
    p.add_argument("--owner", default=None, help="Owner name for OPML head")
    p.add_argument("--debug", action="store_true")
    ns = p.parse_args(argv)
    if ns.debug:
        log.setLevel(logging.DEBUG)
    return {"db": ns.db, "out": ns.out, "owner": ns.owner}

def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_argv(list(sys.argv[1:] if argv is None else argv))
    try:
        export_archive_to_opml(args["db"], args["out"], args["owner"])
        return 0
    except Exception as e:
        log.error("Export failed: %s", e)
        return 2

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

