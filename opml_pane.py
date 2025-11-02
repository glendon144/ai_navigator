# opml_pane.py
# OPML Pane for AI Navigator — generates OPML via aopmlengine and renders it as HTML in Qt.

from __future__ import annotations
from html import escape
from xml.etree import ElementTree as ET

# Qt imports (works with either QWebEngineView or QTextBrowser)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView as _Web
    _USING_WEBENGINE = True
except Exception:
    from PyQt6.QtWidgets import QTextBrowser as _Web
    _USING_WEBENGINE = False

from PyQt6.QtCore import QUrl

# Your Aggressive OPML Engine
import aopmlengine  # must be in PYTHONPATH

class OpmlPane(_Web):
    """
    Drop-in viewer:
      - show_archive(db_path): pulls from AI Navigator archive DB → OPML → HTML
      - show_opml_text(opml_xml): render raw OPML XML
    Wire one instance of this into your main window layout and keep a reference.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        if not _USING_WEBENGINE:
            # QTextBrowser: enable opening external links
            try:
                self.setOpenExternalLinks(True)
            except Exception:
                pass

    # ---- Public API -------------------------------------------------------

    def show_archive(self, db_path: str, owner: str | None = None) -> None:
        """
        Export archive_pages → OPML (via aopmlengine), then render it.
        """
        xml_text = aopmlengine.export_archive_to_opml(
            db_path=db_path,
            out_path=":memory:",  # no real file needed; we just want the XML string
            owner_name=owner,
        )
        self.show_opml_text(xml_text)

    def show_opml_text(self, opml_xml: str) -> None:
        """
        Render an OPML XML string into a collapsible HTML outline.
        """
        html = render_opml_to_html(opml_xml)
        if _USING_WEBENGINE:
            self.setHtml(html, baseUrl=QUrl("about:blank"))
        else:
            self.setHtml(html)

# ---- OPML → HTML renderer -------------------------------------------------

def render_opml_to_html(opml_text: str) -> str:
    """
    Convert OPML (2.0) into readable HTML with <details>/<summary> collapsers.
    Uses only standard libraries for safety.
    """
    try:
        root = ET.fromstring(opml_text)
    except ET.ParseError as e:
        return f"<p><strong>Invalid OPML:</strong> {escape(str(e))}</p>"

    body = root.find("./body")
    if body is None:
        return "<p><strong>Empty OPML:</strong> missing <code>&lt;body&gt;</code>.</p>"

    def walk(node):
        items = []
        for o in node.findall("outline"):
            text = o.get("text") or o.get("title") or "(untitled)"
            # Support your AOPML attrs: url, captured_at, _local_id, snippet child, etc.
            url = o.get("url") or o.get("href")
            captured = o.get("captured_at") or o.get("_captured")
            local_id = o.get("_local_id")

            meta = []
            if captured:
                meta.append(f"<small>{escape(captured)}</small>")
            if local_id:
                meta.append(f"<small>ID {escape(local_id)}</small>")
            if url:
                meta.append(f'<a href="{escape(url)}" target="_blank">source</a>')

            meta_html = (" — " + " · ".join(meta)) if meta else ""
            kids_html = walk(o)

            if kids_html:
                items.append(
                    f"<li><details open><summary>{escape(text)}{meta_html}</summary>{kids_html}</details></li>"
                )
            else:
                items.append(f"<li>{escape(text)}{meta_html}</li>")
        return "<ul>" + "".join(items) + "</ul>" if items else ""

    content = walk(body) or "<p>(No outline items.)</p>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>OPML Preview</title>
  <style>
    :root {{
      --pad: 16px;
    }}
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      line-height: 1.4;
      margin: var(--pad);
    }}
    ul {{ list-style: disc; margin-left: 1.25rem; }}
    details summary {{ cursor: pointer; font-weight: 600; }}
    details > ul {{ margin-top: .5rem; }}
    a {{ text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    small {{ color: #666; }}
    code {{ background: #f3f3f3; padding: .1rem .3rem; border-radius: .25rem; }}
  </style>
</head>
<body>
{content}
</body>
</html>"""


