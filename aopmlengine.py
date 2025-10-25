"""
aopmlengine.py — Aggressive OPML Engine for FunKit / AI Navigator

This version adds a bridge from AI Navigator's archive database
(search_time_machine.db / archive_pages table) into OPML.

You are basically exporting your personal crawl/snapshot history into a
clickable outline that PiKit/FunKit (and any OPML reader) can browse,
with each archived page represented as a node that includes:
- title
- source url
- capture timestamp
- snippet
- structural outline derived from cleaned HTML (Reader Mode)

Features
--------
- HTML parsing respects heading structure (<h1>.. <h6>) and list items.
- Plain text parsing infers section titles and breaks paragraphs into bullets
  when markers are detected; otherwise preserves the text as a single note.
- Optional AI assist hook if ai_interface.py is available.
- Export from SQLite archive_pages → OPML via export_archive_to_opml().

CLI
---
1) Convert a single file (HTML or text) to OPML:
   python aopmlengine.py INPUT_FILE [--title "Custom Title"]
                        [--assume html|text] [--ai]
                        [--out out.opml]

2) Export AI Navigator archive DB to OPML:
   python aopmlengine.py --export-sqlite search_time_machine.db
                         --out archive_export.opml

Notes
-----
- We prefer BeautifulSoup if available. We fall back to a minimal heading parser.
- No external network usage here (other than whatever ai_interface might do).
- OPML 2.0 compliant output.
"""
from __future__ import annotations

import os
import re
import io
import sys
import time
import json
import uuid
import types
import typing as t
import logging
import sqlite3
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as xml_escape

# --- Logging configuration -------------------------------------------------
logger = logging.getLogger("aopmlengine")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.INFO)

# --- Optional imports ------------------------------------------------------
try:
    from bs4 import BeautifulSoup  # type: ignore
    _HAVE_BS4 = True
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    _HAVE_BS4 = False

try:
    ai_interface = __import__("ai_interface")
    _HAVE_AI = True
except Exception:
    ai_interface = None
    _HAVE_AI = False

# --- OPML model ------------------------------------------------------------
@dataclass
class Outline:
    text: str
    children: list["Outline"] = field(default_factory=list)
    _attrs: dict[str, str] = field(default_factory=dict)

    def add(self, child: "Outline") -> None:
        self.children.append(child)

    def to_xml(self, indent: int = 2, level: int = 0) -> str:
        pad = " " * (indent * level)
        attrs = {"text": self.text}
        attrs.update(self._attrs)
        attr_str = " ".join(f"{k}=\"{xml_escape(v)}\"" for k, v in attrs.items())
        if not self.children:
            return f"{pad}<outline {attr_str}/>\n"
        s = io.StringIO()
        s.write(f"{pad}<outline {attr_str}>\n")
        for c in self.children:
            s.write(c.to_xml(indent, level + 1))
        s.write(f"{pad}</outline>\n")
        return s.getvalue()

@dataclass
class OPMLDocument:
    title: str
    date_created: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    owner_name: str | None = None
    outlines: list[Outline] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)

    def to_xml(self, indent: int = 2) -> str:
        head = io.StringIO()
        head.write("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n")
        head.write("<opml version=\"2.0\">\n")
        head.write("  <head>\n")
        head.write(f"    <title>{xml_escape(self.title)}</title>\n")
        head.write(f"    <dateCreated>{xml_escape(self.date_created)}</dateCreated>\n")
        head.write("    <generator>FunKit AOPML Engine</generator>\n")
        if self.owner_name:
            head.write(f"    <ownerName>{xml_escape(self.owner_name)}</ownerName>\n")
        # Additional metadata
        for k, v in self.meta.items():
            head.write(f"    <{k}>{xml_escape(v)}</{k}>\n")
        head.write("  </head>\n")
        head.write("  <body>\n")
        body = io.StringIO()
        for o in self.outlines:
            body.write(o.to_xml(indent=indent, level=1))
        tail = "  </body>\n</opml>\n"
        return head.getvalue() + body.getvalue() + tail

    def add(self, node: Outline) -> None:
        self.outlines.append(node)

# --- Heuristics ------------------------------------------------------------
_HTML_SIGNS = re.compile(r"<\s*(!doctype|html|head|body|h[1-6]|p|div|ul|ol|li)\b", re.I)
_LIST_MARK = re.compile(r"^\s*(?:[-*•‣·]|\d+[.)])\s+")
_TITLE_LINE = re.compile(r"^[\t ]*([A-Z][^a-z\n]{3,}|[#]{1,6}\s+.+)$")


def is_probably_html(text: str) -> bool:
    return bool(_HTML_SIGNS.search(text))


def split_paragraphs(text: str) -> list[str]:
    # Normalize newlines, keep paragraphs separated by blank lines
    parts = re.split(r"\n\s*\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def bulletize_lines(block: str) -> tuple[list[str] | None, str | None]:
    """Try to turn a block into bullets.
    Returns (bullets, remainder). If cannot bulletize, returns (None, original).
    """
    lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return None, None
    if sum(bool(_LIST_MARK.match(ln)) for ln in lines) >= max(2, len(lines) // 2):
        bullets = [re.sub(_LIST_MARK, "", ln).strip() for ln in lines if _LIST_MARK.match(ln)]
        return bullets, None
    # Heuristic: colon-delimited or semicolon list in a sentence
    if ":" in block and any(ch in block for ch in ";,•"):
        head, tail = block.split(":", 1)
        # split by semicolons or bullet symbols
        items = [it.strip(" \t-•‣·") for it in re.split(r"[;•\u2022\u25E6]|\s\-\s", tail) if it.strip()]
        if len(items) >= 2 and all(len(it) <= 240 for it in items):
            return items, head.strip()
    return None, block

# --- HTML parsing ----------------------------------------------------------
class _MiniHTMLHeadingParser:
    """Fallback HTML heading extractor using the standard library."""

    def __init__(self, html: str):
        from html.parser import HTMLParser

        self.headings: list[tuple[int, str]] = []

        class HP(HTMLParser):
            def __init__(self, outer: "_MiniHTMLHeadingParser"):
                super().__init__(convert_charrefs=True)
                self.outer = outer
                self._in_h: int | None = None
                self._buf: list[str] = []

            def handle_starttag(self, tag: str, attrs):
                if tag and len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
                    lvl = int(tag[1])
                    if 1 <= lvl <= 6:
                        self._in_h = lvl
                        self._buf.clear()

            def handle_endtag(self, tag: str):
                if self._in_h and tag == f"h{self._in_h}":
                    text = "".join(self._buf).strip()
                    if text:
                        self.outer.headings.append((self._in_h, re.sub(r"\s+", " ", text)))
                    self._in_h = None
                    self._buf.clear()

            def handle_data(self, data: str):
                if self._in_h:
                    self._buf.append(data)

        HP(self).feed(html)

    def to_outline(self) -> Outline:
        root = Outline("Document")
        stack: list[tuple[int, Outline]] = [(0, root)]
        for lvl, text in self.headings:
            node = Outline(text)
            while stack and lvl <= stack[-1][0]:
                stack.pop()
            stack[-1][1].add(node)
            stack.append((lvl, node))
        return root


def _ensure_wrapped_html(html: str) -> str:
    """
    Sometimes we only have <p>...</p> etc (clean fragments from Reader Mode),
    not a full <html><body>...</body></html>.
    Wrap if needed so BeautifulSoup / fallback parser can reason about it.
    """
    low = html.strip().lower()
    if "<html" in low:
        return html
    # minimal wrapper
    return f"<html><body>{html}</body></html>"


def html_to_outline(html: str) -> Outline:
    html = _ensure_wrapped_html(html)

    if _HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else None
        root = Outline(title or "Document")
        stack: list[tuple[int, Outline]] = [(0, root)]
        body = soup.body or soup
        for el in body.descendants:
            if getattr(el, "name", None) and re.fullmatch(r"h[1-6]", el.name or "", re.I):
                lvl = int(el.name[1])
                text = el.get_text(" ", strip=True)
                node = Outline(text)
                while stack and lvl <= stack[-1][0]:
                    stack.pop()
                stack[-1][1].add(node)
                stack.append((lvl, node))
            elif getattr(el, "name", None) in {"ul", "ol"}:
                # attach list to the most recent heading
                if stack:
                    parent = stack[-1][1]
                    lst = Outline("List")
                    for li in el.find_all("li", recursive=False):
                        txt = li.get_text(" ", strip=True)
                        if txt:
                            lst.add(Outline(txt))
                    if lst.children:
                        parent.add(lst)
        # Capture stray paragraphs at top level
        unsorted = Outline("Unsorted")
        for p in body.find_all("p", recursive=True):
            txt = p.get_text(" ", strip=True)
            if txt:
                unsorted.add(Outline(txt))
        if unsorted.children:
            root.add(unsorted)
        return root
    else:
        logger.debug("bs4 not available; using minimal parser")
        return _MiniHTMLHeadingParser(html).to_outline()

# --- Plain text parsing ----------------------------------------------------

def text_to_outline(text: str, assumed_title: str | None = None) -> Outline:
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Infer title: first underlined or ATX heading (# Title) or ALLCAPS line
    title: str | None = None
    for i, ln in enumerate(lines[:8]):
        if ln.strip().startswith("# "):
            title = ln.strip("# ").strip()
            lines = lines[i + 1 :]
            break
        if i + 1 < len(lines) and set(lines[i + 1].strip()) in ({"="}, {"-"}) and len(lines[i + 1].strip()) >= max(3, len(ln.strip()) // 2):
            title = ln.strip()
            lines = lines[i + 2 :]
            break
        if _TITLE_LINE.match(ln):
            title = ln.strip("# ").strip()
            lines = lines[i + 1 :]
            break
    title = title or assumed_title or "Document"
    root = Outline(title)

    # Group paragraphs
    text_body = "\n".join(lines)
    paras = split_paragraphs(text_body)
    for para in paras:
        bullets, remainder = bulletize_lines(para)
        if bullets is not None:
            # Heading + bullets
            head_text = remainder if remainder else para.split("\n", 1)[0][:60]
            section = Outline(head_text if head_text else "List")
            for b in bullets:
                section.add(Outline(b))
            root.add(section)
        else:
            root.add(Outline(para))
    return root

# --- AI assist -------------------------------------------------------------
@dataclass
class AITask:
    section_id: str
    original: str
    result_xml: str | None = None
    error: str | None = None


def _ai_worker(tasks: list[AITask]):  # pragma: no cover (side-effectful)
    if not _HAVE_AI:
        return
    try:
        for tsk in tasks:
            try:
                # Expected contract: ai_interface.suggest_opml(text:str) -> OPML outline XML (string)
                if hasattr(ai_interface, "suggest_opml"):
                    tsk.result_xml = ai_interface.suggest_opml(tsk.original)
                elif hasattr(ai_interface, "process_text_to_opml"):
                    tsk.result_xml = ai_interface.process_text_to_opml(tsk.original)
                else:
                    tsk.error = "ai_interface lacks suggest_opml/process_text_to_opml"
            except Exception as e:  # noqa: BLE001
                tsk.error = f"AI error: {e}"
    except Exception as e:  # noqa: BLE001
        logger.error("AI worker failed: %s", e)


# --- Public API ------------------------------------------------------------

@dataclass
class EngineConfig:
    enable_ai: bool = False
    owner_name: str | None = None
    owner_email: str | None = None
    title: str | None = None


def _cfg_get(cfg, key, default=""):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def convert_payload_to_opml(title: str, payload, cfg: EngineConfig | dict | None = None) -> str:
    """
    Main entry point used by opml_extras_plugin.
    Detects HTML vs text and routes accordingly.
    Returns XML text.
    """
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload or "")
    low = text.lower()
    if ("<html" in low) or ("<body" in low) or ("<div" in low) or ("<p" in low):
        doc = build_opml_from_html(title, text, cfg=cfg)
    else:
        doc = build_opml_from_text(title, text, cfg=cfg)
    return doc.to_xml()


def build_opml_from_html(title: str, html: str, cfg: EngineConfig | dict | None = None):
    """
    Parse HTML → outline → OPMLDocument (no owner fields).
    We defensively wrap fragment HTML so BeautifulSoup has <body>.
    """
    html = _ensure_wrapped_html(html)
    outline = html_to_outline(html)

    _cfg_title   = _cfg_get(cfg, "title", "")
    _final_title = (outline.text or _cfg_title or title or "Document")

    doc = OPMLDocument(title=_final_title)

    # Transfer structure if any; otherwise add a single node
    for child in getattr(outline, "children", []):
        doc.add(child)
    if not getattr(outline, "children", []):
        doc.add(Outline(outline.text or _final_title))

    return doc


def build_opml_from_text(title: str, text: str, cfg: EngineConfig | dict | None = None):
    """
    Parse plain text → outline → OPMLDocument (no owner fields).
    """
    outline = text_to_outline(text, assumed_title=_cfg_get(cfg, "title", None))

    _cfg_title   = _cfg_get(cfg, "title", "")
    _final_title = (outline.text or _cfg_title or title or "Document")

    doc = OPMLDocument(title=_final_title)

    for child in getattr(outline, "children", []):
        doc.add(child)
    if not getattr(outline, "children", []):
        doc.add(Outline(outline.text or _final_title))

    return doc


def build_opml_from_file(path: str, cfg: EngineConfig | None = None) -> OPMLDocument:
    cfg = cfg or EngineConfig()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()
    if is_probably_html(data):
        logger.info("Detected HTML input")
        return build_opml_from_html(path, data, cfg)
    else:
        logger.info("Detected TEXT input")
        return build_opml_from_text(path, data, cfg)


# --- Archive → OPML bridge -------------------------------------------------

def export_archive_to_opml(
    db_path: str = "search_time_machine.db",
    out_path: str = "archive_export.opml",
    owner_name: str | None = None,
) -> str:
    """
    Read AI Navigator's archive_pages table and emit a single OPML file.

    Each archived page becomes a top-level <outline> with attributes:
      text        = title
      url         = original URL
      captured_at = timestamp
      _local_id   = the row id in archive_pages (so PiKit can "Recover")
    Children:
      - Snippet: first ~200 chars of snippet
      - Structured headings/lists extracted from clean_html (Reader Mode)
        using build_opml_from_html()

    Returns the OPML XML string (and writes it to out_path).
    """
    # Connect and pull rows
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, url, captured_at, snippet, clean_html
        FROM archive_pages
        ORDER BY captured_at DESC;
        """
    )
    rows = cur.fetchall()
    conn.close()

    # Build OPML doc
    doc = OPMLDocument(
        title="AI Navigator Archive",
        date_created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        owner_name=owner_name,
        meta={
            "generatorDetail": "AI Navigator Reader Mode + FunKit AOPML Engine",
            "about": "Locally captured pages, cleaned of scripts/paywalls, exported as outline for PiKit/FunKit.",
        },
    )

    for (row_id, title, url, captured_at, snippet, clean_html) in rows:
        page_title = title or f"Snapshot {row_id}"

        # Top-level node for this capture
        page_node = Outline(
            page_title,
            _attrs={
                "url": url or "",
                "captured_at": captured_at or "",
                "_local_id": str(row_id),
            },
        )

        # Add snippet summary as first child
        if snippet:
            snippet_txt = f"Snippet: {snippet[:200]}…"
            page_node.add(Outline(snippet_txt))

        # Add structured breakdown of the cleaned article
        if clean_html:
            subdoc = build_opml_from_html(page_title, clean_html)
            for c in subdoc.outlines:
                page_node.add(c)

        doc.add(page_node)

    xml_text = doc.to_xml()

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml_text)

    logger.info("Wrote OPML export: %s", out_path)
    return xml_text


# --- CLI ------------------------------------------------------------------

def _parse_argv(argv: list[str]) -> dict:
    import argparse

    p = argparse.ArgumentParser(description="FunKit AOPML Engine — HTML/TEXT to OPML or SQLite archive export")
    p.add_argument("input", nargs="?", help="Input file path (HTML or text). Not required if using --export-sqlite.")
    p.add_argument("--title", dest="title", default=None, help="Override OPML title for single-file conversion")
    p.add_argument("--assume", choices=["html", "text"], help="Assume input type, bypass detection (single-file mode)")
    p.add_argument("--ai", action="store_true", help="Enable AI assist if ai_interface is present (not used in export-sqlite)")
    p.add_argument("--owner", default=None, help="Owner name for OPML head / export")
    p.add_argument("--out", default=None, help="Output .opml path")
    p.add_argument("--export-sqlite", dest="export_sqlite", default=None,
                   help="Instead of converting a single file, export the AI Navigator archive SQLite DB at this path.")
    p.add_argument("--debug", action="store_true", help="Verbose logs")
    ns = p.parse_args(argv)
    if ns.debug:
        logger.setLevel(logging.DEBUG)
    return {
        "input": ns.input,
        "title": ns.title,
        "assume": ns.assume,
        "enable_ai": bool(ns.ai),
        "owner": ns.owner,
        "out": ns.out,
        "export_sqlite": ns.export_sqlite,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv or sys.argv[1:])

    # Mode 1: export whole archive DB → OPML
    if args["export_sqlite"]:
        db_path = args["export_sqlite"]
        out_path = args["out"] or "archive_export.opml"
        export_archive_to_opml(
            db_path=db_path,
            out_path=out_path,
            owner_name=args["owner"],
        )
        return 0

    # Mode 2: convert a single file
    if not args["input"]:
        logger.error("No input file provided and no --export-sqlite specified.")
        return 2

    cfg = EngineConfig(enable_ai=args["enable_ai"], owner_name=args["owner"], title=args["title"])
    path = args["input"]
    if not os.path.exists(path):
        logger.error("Input not found: %s", path)
        return 2

    # Bypass detection if requested
    if args["assume"] == "html":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        doc = build_opml_from_html(args["title"] or path, html, cfg)
    elif args["assume"] == "text":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        doc = build_opml_from_text(args["title"] or path, txt, cfg)
    else:
        # autodetect
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        if is_probably_html(data):
            logger.info("Detected HTML input")
            doc = build_opml_from_html(args["title"] or path, data, cfg)
        else:
            logger.info("Detected TEXT input")
            doc = build_opml_from_text(args["title"] or path, data, cfg)

    out_path = args["out"] or os.path.splitext(path)[0] + ".opml"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc.to_xml())
    logger.info("Wrote OPML: %s", out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

