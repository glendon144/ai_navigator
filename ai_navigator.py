#!/usr/bin/env python3
#
# ai_navigator.py
#
# AI Navigator prototype (Archive + Recover + Reader Mode + OPML Outline + Reload + Recover-to-ChatGPT + Recover Memory Weave + Memory Pane)
#
# Layout:
#   [ BrowserPane | ResultsPane | OutlinePane | MemoryPane ]
#
# Capabilities:
#   - Archive: capture current page into SQLite (raw + Reader Mode clean_html).
#   - Recover: load a stored snapshot into the browser offline.
#   - Recover to ChatGPT: copy a compact Context Capsule for the selected snapshot
#                         and open chatgpt.com for a paste-and-go resume.
#   - Recover Memory Weave: copy a 3-item recent thread (prefer same domain) and open chatgpt.com.
#   - Outline: browse archive_export.opml as a clickable knowledge tree.
#       Clicking a node with _local_id pulls that snapshot from SQLite
#       and renders it offline in BrowserPane.
#   - Reload Outline: re-parse archive_export.opml without restarting.
#   - Memory Pane: separate memory.db that logs URL/title/html on each page load.
#
# You are now browsing history, not the feed.

import sys
import re
import os
import webbrowser
import subprocess
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import threading
import time

from PySide6.QtCore import (
    Qt,
    QSize,
    QTimer,
    QRect,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QPixmap,
    QPainter,
    QPen,
    QBrush,
    QColor,
    QTransform,
    QPainterPath,
    QGuiApplication,
    QDesktopServices,
    QClipboard,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QLabel,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from bs4 import BeautifulSoup  # for OPML export parsing

# Initializes storage/ and ensures archive_pages exists (same schema used below).
from init_db import init_db_if_needed

init_db_if_needed()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Main archive DB (unchanged)
DB_PATH = Path("storage") / "search_time_machine.db"
DEFAULT_OPML_PATH = "archive_export.opml"

# Separate memory DB (M1)
MEMORY_DB_PATH = Path("memory.db")

K_WEAVE = 3  # Recover Memory Weave count


# ---------------------------------------------------------------------------
# Archive DB helpers (archive_pages)
# ---------------------------------------------------------------------------

def ensure_archive_table(db_path: Path):
    """
    Make sure the archive_pages table exists (matches init_db.py).
    Columns:
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        title TEXT,
        captured_at TEXT,
        snippet TEXT,
        html TEXT,
        clean_html TEXT
    """
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
    try:
        cur.execute("ALTER TABLE archive_pages ADD COLUMN clean_html TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def html_to_snippet(html: str, max_len: int = 500) -> str:
    """
    Tiny text extractor for preview/snippet:
    - strips <script> and <style>
    - strips other tags
    - collapses whitespace
    Returns first max_len chars.
    """
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text[:max_len]


def sanitize_html_for_reader(raw_html: str) -> str:
    """
    Reader Mode: preserves narrative, removes instrumentation.
    We strip:
      - <script>...</script>
      - <iframe>...</iframe>
      - preload / preconnect / dns-prefetch link tags
      - inline JS event handlers like onclick="..."
    """
    cleaned = re.sub(
        r"<script.*?</script>", "", raw_html, flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = re.sub(
        r"<iframe.*?</iframe>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = re.sub(
        r"<link[^>]+rel=[\"']?(preload|dns-prefetch|preconnect|modulepreload)[\"']?[^>]*>",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\son\w+\s*=\s*['\"].*?['\"]", "", cleaned, flags=re.IGNORECASE | re.DOTALL
    )
    return cleaned


def save_archive_page(db_path: Path, url: str, title: str, html: str):
    """
    Insert a captured page into archive_pages with timestamp + snippet.
    Also stores a sanitized Reader Mode copy (clean_html).
    """
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
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Memory DB helpers (memory.db)
# ---------------------------------------------------------------------------

def ensure_memory_table(db_path: Path):
    """
    Simple memory table for the Memory Pane:
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        title TEXT,
        timestamp TEXT,
        raw_html TEXT
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT,
            timestamp TEXT,
            raw_html TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def log_memory_entry(db_path: Path, url: str, title: str, raw_html: str):
    ensure_memory_table(db_path)
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO memory_entries (url, title, timestamp, raw_html)
        VALUES (?, ?, ?, ?);
        """,
        (url, title, ts, raw_html),
    )
    conn.commit()
    conn.close()


def load_memory_entries(db_path: Path, limit: int = 200):
    ensure_memory_table(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, url, title, timestamp
        FROM memory_entries
        ORDER BY id DESC
        LIMIT ?;
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Clipboard helper (Qt + X11 fallbacks)
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> bool:
    """
    Try Qt clipboard (Clipboard + Selection), then fall back to xclip/xsel on X11.
    Returns True if we *believe* it landed on a clipboard.
    """
    ok = False
    try:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text or "")
            try:
                cb.setText(text or "", mode=QClipboard.Mode.Selection)
            except Exception:
                pass
            ok = True
    except Exception:
        ok = False

    if ok:
        return True

    for cmd in (
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        try:
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            p.communicate(input=(text or "").encode("utf-8"), timeout=1.5)
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# OpenVPN controller
# ---------------------------------------------------------------------------

class VPNController:
    """
    Minimal controller for a single OpenVPN client managed by systemd:
      openvpn-client@ainav.service
    """

    def __init__(self, unit_name="openvpn-client@ainav"):
        self.unit = unit_name

    def _run(self, *args, check=False):
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=check,
        )

    def is_active(self) -> bool:
        r = self._run("systemctl", "is-active", "--quiet", self.unit)
        return r.returncode == 0

    def start(self) -> bool:
        self._run("systemctl", "start", self.unit)
        return self.is_active()

    def stop(self) -> bool:
        self._run("systemctl", "stop", self.unit)
        return not self.is_active()

    def _default_route_iface(self) -> str | None:
        r = self._run("ip", "route")
        for line in r.stdout.splitlines():
            if line.startswith("default "):
                parts = line.split()
                if "dev" in parts:
                    try:
                        idx = parts.index("dev")
                        return parts[idx + 1]
                    except Exception:
                        pass
        return None

    def has_tun(self) -> bool:
        iface = self._default_route_iface()
        if iface and iface.startswith("tun"):
            return True
        r = self._run("ip", "addr")
        return " tun0:" in r.stdout or " tun" in r.stdout

    def ensure_connected(self, timeout_s=20) -> bool:
        if self.is_active() and self.has_tun():
            return True
        self.start()
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if self.is_active() and self.has_tun():
                return True
            time.sleep(0.5)
        return False


# ---------------------------------------------------------------------------
# OPML export helpers
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    s = re.sub(r"\s+", "-", (s or "").strip())
    s = re.sub(r"[^A-Za-z0-9\-_]+", "", s)
    return s or "page"


def _html_to_opml(html: str, title: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    doc_title = (title or (soup.title.string if soup.title else "")) or "Untitled"
    nodes = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(tag.name[1])
        text = tag.get_text(" ", strip=True)
        if text:
            nodes.append((level, text))

    out = [
        '<?xml version="1.0"?>',
        '<opml version="2.0"><head>',
        f"<title>{doc_title}</title>",
        "</head><body>",
    ]

    stack = [0]
    for level, text in nodes:
        while stack and level <= stack[-1]:
            out.append("</outline>")
            stack.pop()
        out.append(f'<outline text="{text}">')
        stack.append(level)

    while len(stack) > 1:
        out.append("</outline>")
        stack.pop()

    out.append("</body></opml>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Throbber
# ---------------------------------------------------------------------------

class ThrobberWidget(QWidget):
    """
    Rotating "A" throbber for AI Navigator.
    """

    def __init__(self, parent=None, size=24):
        super().__init__(parent)
        self.setFixedSize(QSize(size, size))
        self.angle = 0

        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

        self.base_pixmap = self._make_base_pixmap(size)

    def _make_base_pixmap(self, size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)

        circle_color = QColor(0, 60, 90)
        painter.setBrush(QBrush(circle_color))
        painter.setPen(QPen(QColor(200, 230, 255), 1))
        painter.drawEllipse(QRect(1, 1, size - 2, size - 2))

        painter.setPen(Qt.white)
        painter.setBrush(Qt.white)

        w = size
        h = size

        tri_path = QPainterPath()
        tri_path.moveTo(0.5 * w, 0.18 * h)
        tri_path.lineTo(0.18 * w, 0.85 * h)
        tri_path.lineTo(0.82 * w, 0.85 * h)
        tri_path.closeSubpath()
        painter.drawPath(tri_path)

        bar_x = 0.33 * w
        bar_y = 0.55 * h
        bar_w = 0.34 * w
        bar_h = 0.12 * h
        painter.fillRect(
            QRect(int(bar_x), int(bar_y), int(bar_w), int(bar_h)),
            Qt.white,
        )

        painter.end()
        return pm

    def _tick(self):
        self.angle = (self.angle + 15) % 360
        self.update()

    def start(self):
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        if self.timer.isActive():
            self.timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0

        t = QTransform()
        t.translate(cx, cy)
        t.rotate(self.angle)
        t.translate(-cx, -cy)

        rotated = self.base_pixmap.transformed(t, Qt.SmoothTransformation)

        x = (self.width() - rotated.width()) / 2.0
        y = (self.height() - rotated.height()) / 2.0
        painter.drawPixmap(int(x), int(y), rotated)
        painter.end()


# ---------------------------------------------------------------------------
# Browser Pane
# ---------------------------------------------------------------------------

class BrowserPane(QWidget):
    """
    Left pane:
      Toolbar (Back, Forward, Reload, Home, URL, Go, Archive, OPML export, VPN, Throbber)
      QWebEngineView
      Status line

    New: on_memory_log callback for Memory Pane.
    """

    def __init__(self, on_page_loaded=None, on_archive_request=None, on_memory_log=None):
        super().__init__()

        self.on_page_loaded = on_page_loaded
        self.on_archive_request = on_archive_request
        self.on_memory_log = on_memory_log

        self.view = QWebEngineView()

        self.url_bar = QLineEdit()
        self.go_button = QPushButton("Go")
        self.back_button = QPushButton("←")
        self.fwd_button = QPushButton("→")
        self.reload_button = QPushButton("Reload")
        self.home_button = QPushButton("Home")
        self.archive_button = QPushButton("Archive")
        self.opml_button = QPushButton("Outline (OPML export)")
        self.throbber = ThrobberWidget(size=24)

        # --- VPN UI ---
        self.vpn = VPNController()
        self.require_vpn = False
        self.vpn_button = QPushButton("VPN")
        self.vpn_button.setCheckable(True)
        self.vpn_status = QLabel("●")

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(
            "font-size: 11px; color: #d0e8ff; background-color: #003c5a; padding: 2px;"
        )
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        toolbar_row = QHBoxLayout()
        toolbar_bg = QWidget()
        toolbar_bg.setStyleSheet(
            "background-color: #003c5a; color: white; border-bottom: 1px solid #99ccee;"
        )
        toolbar_bg.setLayout(toolbar_row)

        btn_style = (
            "QPushButton {"
            "  background-color: #195b7e;"
            "  color: #ffffff;"
            "  border: 1px solid #99ccee;"
            "  padding: 3px 6px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #0f3b52;"
            "}"
        )
        for b in (
            self.back_button,
            self.fwd_button,
            self.reload_button,
            self.home_button,
            self.go_button,
            self.archive_button,
            self.opml_button,
            self.vpn_button,
        ):
            b.setStyleSheet(btn_style)

        self.vpn_status.setStyleSheet("color: red; padding-left:6px;")

        self.url_bar.setStyleSheet(
            "QLineEdit {"
            "  background-color: #dfefff;"
            "  color: #000000;"
            "  border: 1px solid #99ccee;"
            "  padding: 2px 4px;"
            "}"
        )

        toolbar_row.addWidget(QLabel("AI Navigator", parent=toolbar_bg))
        toolbar_row.addWidget(self.back_button)
        toolbar_row.addWidget(self.fwd_button)
        toolbar_row.addWidget(self.reload_button)
        toolbar_row.addWidget(self.home_button)
        toolbar_row.addWidget(QLabel("URL:", parent=toolbar_bg))
        toolbar_row.addWidget(self.url_bar, stretch=1)
        toolbar_row.addWidget(self.go_button)
        toolbar_row.addWidget(self.archive_button)
        toolbar_row.addWidget(self.opml_button)
        toolbar_row.addWidget(self.vpn_button)
        toolbar_row.addWidget(self.vpn_status)
        toolbar_row.addWidget(self.throbber)

        status_row = QHBoxLayout()
        status_bg = QWidget()
        status_bg.setStyleSheet(
            "background-color: #003c5a; border-top: 1px solid #99ccee;"
        )
        status_bg.setLayout(status_row)
        status_row.addWidget(self.status_label)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar_bg)
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(status_bg)
        self.setLayout(layout)

        self.home_url = "https://www.google.com/"
        self.go_button.clicked.connect(self.load_url)
        self.url_bar.returnPressed.connect(self.load_url)
        self.back_button.clicked.connect(self.view.back)
        self.fwd_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self.view.reload)
        self.home_button.clicked.connect(self.load_home)
        self.archive_button.clicked.connect(self._archive_current_page)
        self.opml_button.clicked.connect(self._export_outline_opml)

        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self._on_load_progress)
        self.view.loadFinished.connect(self._on_load_finished)

        self.vpn_button.toggled.connect(self._toggle_vpn)
        self.vpn_timer = QTimer(self)
        self.vpn_timer.timeout.connect(self._refresh_vpn_status)
        self.vpn_timer.start(1500)

        self.url_bar.setText(self.home_url)
        self.load_url()

    # VPN helpers
    def _toggle_vpn(self, checked: bool):
        self.require_vpn = checked
        if checked:
            threading.Thread(target=self._bring_vpn_up, daemon=True).start()
        else:
            self.vpn.stop()
            self._refresh_vpn_status()

    def _bring_vpn_up(self):
        ok = self.vpn.ensure_connected(timeout_s=25)
        self.status_label.setText("VPN connected" if ok else "VPN connection failed")
        self._refresh_vpn_status()

    def _refresh_vpn_status(self):
        active = self.vpn.is_active()
        has_tun = self.vpn.has_tun()
        color = "green" if (active and has_tun) else ("orange" if active else "red")
        self.vpn_status.setStyleSheet(f"color: {color}; padding-left:6px;")
        self.vpn_status.setToolTip(
            f"VPN: {'active' if active else 'inactive'}; tun: {'present' if has_tun else 'missing'}"
        )

    def load_home(self):
        self.url_bar.setText(self.home_url)
        self.load_url()

    def load_url(self):
        url = self.url_bar.text().strip()
        if not url.startswith("http"):
            url = "https://" + url

        if self.require_vpn:
            if not (self.vpn.is_active() and self.vpn.has_tun()):
                self.status_label.setText("Waiting for VPN…")
                threading.Thread(target=self._bring_vpn_up, daemon=True).start()
                return
        self.view.setUrl(QUrl(url))

    def load_html_snapshot(self, html: str, base_url: str):
        self.view.setHtml(html, baseUrl=QUrl(base_url))
        self.status_label.setText("Loaded Reader-Mode snapshot (offline).")
        self.url_bar.setText(base_url)

    def _on_load_started(self):
        self.throbber.start()
        self.status_label.setText("Contacting host...")

    def _on_load_progress(self, pct: int):
        self.status_label.setText(f"Transferring data... {pct}%")

    def _on_load_finished(self, ok: bool):
        self.throbber.stop()
        if not ok:
            self.status_label.setText("Load failed.")
            QMessageBox.warning(self, "Load error", "Page failed to load.")
            return

        current_url = self.view.url().toString()
        self.url_bar.setText(current_url)
        self.status_label.setText("Done.")

        # Notify basic page-loaded event
        if self.on_page_loaded:
            self.on_page_loaded(current_url)

        # Automatic memory logging (url, title, raw_html)
        if self.on_memory_log:
            def _got_html(html_str: str):
                title = self.view.title() or current_url
                self.on_memory_log(current_url, title, html_str)

            self.view.page().toHtml(_got_html)

    def load_from_memory(self, url: str):
        """Load a URL initiated from the Memory Pane selection."""
        if not url:
            return
        self.url_bar.setText(url)
        self.load_url()

    def _archive_current_page(self):
        current_url = self.view.url().toString()
        current_title = self.view.title() or current_url

        def got_html(html_str):
            if self.on_archive_request:
                self.on_archive_request(current_url, current_title, html_str)

        self.view.page().toHtml(got_html)

    # OPML export
    def _export_outline_opml(self):
        """Export the visible page's heading outline to ./archives/opml/*.opml"""

        def _on_html(html: str):
            try:
                title = self.view.title() or ""
                opml = _html_to_opml(html, title)
                outdir = Path.cwd() / "archives" / "opml"
                outdir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                name = f"{_slug(title)}-{ts}.opml"
                outpath = outdir / name
                outpath.write_text(opml, encoding="utf-8")
                self.status_label.setText(f"OPML saved → {outpath}")
                QMessageBox.information(self, "OPML export", f"Saved:\n{outpath}")
            except Exception as e:
                QMessageBox.critical(self, "OPML export failed", str(e))

        self.view.page().toHtml(_on_html)


# ---------------------------------------------------------------------------
# Results Pane
# ---------------------------------------------------------------------------

class ResultsPane(QWidget):
    """
    Snapshot list pane.
    """

    recoveredPage = Signal(str, str)  # html, url

    def __init__(self, db_path: Path):
        super().__init__()

        self.db_path = db_path
        self.conn = None

        self.archive_list = QListWidget()
        self.details_list = QListWidget()

        self.recover_button = QPushButton("Recover")
        self.recover_chat_button = QPushButton("Recover to ChatGPT")
        self.recover_weave_button = QPushButton("Recover Memory Weave")

        header_label = QLabel("Archived Pages")
        header_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )
        details_label = QLabel("Details")
        details_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )

        btn_style = (
            "QPushButton {"
            "  background-color: #195b7e;"
            "  color: #ffffff;"
            "  border: 1px solid #99ccee;"
            "  padding: 3px 6px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #0f3b52;"
            "}"
        )
        for b in (
            self.recover_button,
            self.recover_chat_button,
            self.recover_weave_button,
        ):
            b.setStyleSheet(btn_style)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(header_label)
        layout.addWidget(self.archive_list, stretch=1)

        details_header_row = QHBoxLayout()
        details_header_row.addWidget(details_label)
        details_header_row.addStretch(1)
        details_header_row.addWidget(self.recover_weave_button)
        details_header_row.addWidget(self.recover_chat_button)
        details_header_row.addWidget(self.recover_button)

        layout.addLayout(details_header_row)
        layout.addWidget(self.details_list, stretch=2)

        self.setLayout(layout)

        self.archive_list.currentItemChanged.connect(self._populate_details_for_archive)
        self.recover_button.clicked.connect(self._recover_selected)
        self.recover_chat_button.clicked.connect(self._recover_to_chatgpt_selected)
        self.recover_weave_button.clicked.connect(self._recover_memory_weave_selected)

        self._ensure_connection()
        self._populate_archive_list()

    def _ensure_connection(self):
        if self.conn is None:
            ensure_archive_table(self.db_path)
            self.conn = sqlite3.connect(self.db_path)

    def _populate_archive_list(self):
        self.archive_list.clear()
        if self.conn is None:
            return
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, title, captured_at
            FROM archive_pages
            ORDER BY captured_at DESC
            LIMIT 200;
            """
        )
        for page_id, title, captured_at in cur.fetchall():
            label = f"{title}    ({captured_at})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, page_id)
            self.archive_list.addItem(item)

    def _populate_details_for_archive(
        self, current: QListWidgetItem, previous: QListWidgetItem
    ):
        self.details_list.clear()
        if self.conn is None or current is None:
            return
        page_id = current.data(Qt.UserRole)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT url, snippet FROM archive_pages WHERE id = ?;",
            (page_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        url, snippet = row
        preview_text = f"{url}\n\n{snippet}"
        self.details_list.addItem(QListWidgetItem(preview_text))

    def _recover_selected(self):
        if self.conn is None:
            QMessageBox.warning(self, "No DB", "Database not available.")
            return
        current_item = self.archive_list.currentItem()
        if current_item is None:
            QMessageBox.information(
                self, "No selection", "Select an archived page first."
            )
            return
        page_id = current_item.data(Qt.UserRole)
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT url, COALESCE(clean_html, html)
            FROM archive_pages
            WHERE id = ?;
            """,
            (page_id,),
        )
        row = cur.fetchone()
        if not row:
            QMessageBox.warning(
                self,
                "Not found",
                "That archived page no longer exists in the database.",
            )
            return
        url, html_for_reader = row
        self.recoveredPage.emit(html_for_reader, url)

    def _recover_to_chatgpt_selected(self):
        try:
            if self.conn is None:
                QMessageBox.warning(self, "No DB", "Database not available.")
                return
            item = self.archive_list.currentItem()
            if item is None:
                QMessageBox.information(
                    self, "No selection", "Select an archived page first."
                )
                return

            page_id = item.data(Qt.UserRole)
            cur = self.conn.cursor()
            cur.execute(
                """
                SELECT title, url, captured_at, snippet, COALESCE(clean_html, html)
                FROM archive_pages
                WHERE id = ?;
                """,
                (page_id,),
            )
            row = cur.fetchone()
            if not row:
                QMessageBox.warning(
                    self, "Not found", "That archived page no longer exists."
                )
                return

            title, url, captured_at, snippet, body = row
            capsule = build_context_capsule_for_snapshot(
                title=title or url or "(untitled)",
                url=url or "about:blank",
                captured_at=captured_at or "",
                snippet=snippet or "",
                body=body or "",
                hard_cap_chars=6500,
            )

            copied = copy_to_clipboard(capsule)

            target = "https://chatgpt.com/"
            opened = QDesktopServices.openUrl(QUrl(target))
            if not opened:
                if not webbrowser.open_new_tab(target):
                    try:
                        subprocess.Popen(
                            ["xdg-open", target],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass

            if copied:
                QMessageBox.information(
                    self,
                    "Capsule ready",
                    "Context Capsule copied to clipboard.\n"
                    "Switch to the ChatGPT tab and paste to resume.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Clipboard problem",
                    "Couldn't access the system clipboard.\n\n"
                    "Tip: install xclip or xsel (Linux) for a reliable fallback,\n"
                    "or just paste from the last successful copy if it's still there.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Recover to ChatGPT failed", str(e))

    def _recover_memory_weave_selected(self):
        try:
            if self.conn is None:
                QMessageBox.warning(self, "No DB", "Database not available.")
                return
            item = self.archive_list.currentItem()
            if item is None:
                QMessageBox.information(
                    self, "No selection", "Select an archived page first."
                )
                return

            page_id = item.data(Qt.UserRole)

            capsule = build_memory_weave_packet(
                self.conn, page_id, k=K_WEAVE, hard_cap_chars=7000
            )

            copied = copy_to_clipboard(capsule)

            target = "https://chatgpt.com/"
            opened = QDesktopServices.openUrl(QUrl(target))
            if not opened:
                if not webbrowser.open_new_tab(target):
                    try:
                        subprocess.Popen(
                            ["xdg-open", target],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass

            if copied:
                QMessageBox.information(
                    self,
                    "Weave ready",
                    "Memory Weave copied to clipboard (k=3).\n"
                    "Switch to the ChatGPT tab and paste to resume.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Clipboard problem",
                    "Couldn't access the system clipboard.\n\n"
                    "Tip: install xclip or xsel (Linux) for a reliable fallback.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Recover Memory Weave failed", str(e))

    def refresh_all(self):
        if self.conn is None:
            self._ensure_connection()
        self._populate_archive_list()


# ---------------------------------------------------------------------------
# Capsule builders
# ---------------------------------------------------------------------------

def _clean_for_capsule(s: str) -> str:
    s = s.replace("```", "ʼʼʼ")
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

    snippet_block = ""
    if snippet:
        snippet_block = f"**Snippet**\n{snippet}\n\n"

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
    current_page_id: int,
    k: int = 3,
    hard_cap_chars: int = 7000,
) -> str:
    cur = conn.cursor()

    cur.execute(
        "SELECT url, title, captured_at, snippet FROM archive_pages WHERE id = ?;",
        (current_page_id,),
    )
    row = cur.fetchone()
    if not row:
        return build_global_weave_packet(conn, k=k, hard_cap_chars=hard_cap_chars)

    sel_url, sel_title, sel_captured_at, sel_snippet = row
    domain = urlparse(sel_url or "").netloc.lower()

    items = []

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


def build_global_weave_packet(
    conn: sqlite3.Connection, k: int = 3, hard_cap_chars: int = 7000
) -> str:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, url, captured_at, snippet
        FROM archive_pages
        ORDER BY captured_at DESC
        LIMIT ?;
        """,
        (k,),
    )
    rows = cur.fetchall()

    header = "### Context Capsule — ai_navigator\nThread scope: global\n"
    header += f"Captured: {datetime.utcnow().isoformat(timespec='seconds')}Z\n---\n"

    lines = []
    for _id, title, url, ts, snip in rows:
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


# ---------------------------------------------------------------------------
# Outline Pane
# ---------------------------------------------------------------------------

class OutlinePane(QWidget):
    """
    OPML Outline browser.
    """

    def __init__(
        self, db_path: Path, on_open_local=None, opml_path: str = DEFAULT_OPML_PATH
    ):
        super().__init__()

        self.db_path = db_path
        self.on_open_local = on_open_local
        self.opml_path = opml_path

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)

        self.reload_button = QPushButton("Reload")
        self.reload_button.setStyleSheet(
            "QPushButton {"
            "  background-color: #195b7e;"
            "  color: #ffffff;"
            "  border: 1px solid #99ccee;"
            "  padding: 3px 6px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #0f3b52;"
            "}"
        )

        header_label = QLabel("Outline (OPML export)")
        header_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )

        header_row = QHBoxLayout()
        header_row.addWidget(header_label)
        header_row.addStretch(1)
        header_row.addWidget(self.reload_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_row)
        layout.addWidget(self.tree, stretch=1)
        self.setLayout(layout)

        self._populate_tree_from_opml()

        self.tree.itemActivated.connect(self._handle_activate)
        self.reload_button.clicked.connect(self.reload_outline)

    def _populate_tree_from_opml(self):
        self.tree.clear()
        try:
            doc = ET.parse(self.opml_path)
        except Exception as e:
            warn_item = QTreeWidgetItem([f"(no outline loaded: {e})"])
            self.tree.addTopLevelItem(warn_item)
            return

        body = doc.getroot().find("./body")
        if body is None:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(empty outline body)"]))
            return

        def add_outline_element(xml_el, parent_item=None):
            if xml_el.tag != "outline":
                return
            text = xml_el.attrib.get("text", "(untitled)")
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.UserRole, xml_el.attrib)
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for child in xml_el.findall("./outline"):
                add_outline_element(child, item)

        for top in body.findall("./outline"):
            add_outline_element(top, None)

        self.tree.expandToDepth(1)

    def reload_outline(self):
        self._populate_tree_from_opml()

    def _handle_activate(self, item, column):
        attrs = item.data(0, Qt.UserRole) or {}
        local_id = attrs.get("_local_id")
        if local_id and self.on_open_local:
            try:
                self.on_open_local(int(local_id))
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Memory Pane (replaces AssistantPane)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Memory Pane (Session → Domain → Page, Tree-Based — fixed)
# ---------------------------------------------------------------------------
class MemoryPane(QWidget):
    """
    Memory Pane (Tree-Based):
      Session (per hour) → Domain → Page entries
    """

    openUrlRequested = Signal(str)

    def __init__(self, db_path: Path):
        super().__init__()
        self.db_path = db_path

        # Header
        header_label = QLabel("Memory Tree")
        header_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; "
            "color: #ffffff; padding: 4px;"
        )

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(
            "background-color: #1e1e1e; color: #c0ffc0; "
            "font-family: monospace;"
        )

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet(
            "QPushButton {"
            "  background-color: #195b7e;"
            "  color: #ffffff;"
            "  border: 1px solid #99ccee;"
            "  padding: 3px 6px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:pressed { background-color: #0f3b52; }"
        )

        # Layout
        header_row = QHBoxLayout()
        header_row.addWidget(header_label)
        header_row.addStretch(1)
        header_row.addWidget(self.refresh_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_row)
        layout.addWidget(self.tree, stretch=1)
        self.setLayout(layout)

        # Wire button
        self.refresh_button.clicked.connect(self.refresh)
        self.tree.itemClicked.connect(self._handle_item_click)
        self.refresh()

    # ------------------------------------------------------------------
    # Render Memory Tree
    # ------------------------------------------------------------------
    def refresh(self):
        rows = load_memory_entries(self.db_path, limit=200)
        self.tree.clear()

        # sessions = { session_hour: { domain: [(mid,title,url,ts), ...] } }
        sessions = {}

        for mid, url, title, ts in rows:
            if not ts:
                continue

            # Normalize timestamps to the hour
            try:
                dt = datetime.fromisoformat(ts.replace("Z", ""))
                session_key = dt.strftime("%Y-%m-%d %H:00")
            except Exception:
                session_key = "Unknown Session"

            domain = urlparse(url).netloc or "unknown-domain"

            sessions.setdefault(session_key, {})
            sessions[session_key].setdefault(domain, [])
            sessions[session_key][domain].append((mid, title or "(No title)", url, ts))

        # Build display tree
        for session_key in sorted(sessions.keys(), reverse=True):
            session_item = QTreeWidgetItem([session_key])
            self.tree.addTopLevelItem(session_item)

            for domain in sorted(sessions[session_key].keys()):
                domain_item = QTreeWidgetItem([domain])
                session_item.addChild(domain_item)

                for mid, title, url, ts in sessions[session_key][domain]:
                    label = f"[{mid}] {title}"
                    page_item = QTreeWidgetItem([label])
                    page_item.setToolTip(0, url)
                    domain_item.addChild(page_item)

        self.tree.expandToDepth(1)

    def _handle_item_click(self, item, column):
        """When a leaf (page) is clicked, emit its URL for the browser to load."""
        url = item.toolTip(0)
        if url:
            self.openUrlRequested.emit(url)


    # ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QWidget):
    """
    4-pane layout:
      BrowserPane | ResultsPane | OutlinePane | MemoryPane
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("AI Navigator")
        self.setMinimumSize(QSize(1600, 900))

        self.results_pane = ResultsPane(DB_PATH)
        self.memory_pane = MemoryPane(MEMORY_DB_PATH)
        self.browser_pane = BrowserPane(
            on_page_loaded=self._handle_page_loaded,
            on_archive_request=self._handle_archive_request,
            on_memory_log=self._handle_memory_log,
        )
        self.outline_pane = OutlinePane(
            DB_PATH,
            on_open_local=self._open_local_snapshot_by_id,
            opml_path=DEFAULT_OPML_PATH,
        )

        self.results_pane.recoveredPage.connect(self._handle_recovered_page)
        self.memory_pane.openUrlRequested.connect(self.browser_pane.load_from_memory)

        mid_splitter = QSplitter(Qt.Horizontal)
        mid_splitter.addWidget(self.results_pane)
        mid_splitter.addWidget(self.outline_pane)
        mid_splitter.addWidget(self.memory_pane)
        mid_splitter.setSizes([300, 300, 400])

        outer_splitter = QSplitter(Qt.Horizontal)
        outer_splitter.addWidget(self.browser_pane)
        outer_splitter.addWidget(mid_splitter)
        outer_splitter.setSizes([900, 700])

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(outer_splitter)
        self.setLayout(main_layout)

    def _handle_page_loaded(self, url_str: str):
        # Hook point for future auto-archive/diff logic.
        pass

    def _handle_archive_request(self, url: str, title: str, html: str):
        save_archive_page(DB_PATH, url, title, html)
        self.results_pane.refresh_all()
        # After regenerating archive_export.opml externally,
        # hit "Reload" in OutlinePane to see new items.

    def _handle_memory_log(self, url: str, title: str, html: str):
        log_memory_entry(MEMORY_DB_PATH, url, title, html)
        self.memory_pane.refresh()

    def _handle_recovered_page(self, html: str, url: str):
        self.browser_pane.load_html_snapshot(html, url)

    def _open_local_snapshot_by_id(self, row_id: int):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT url, COALESCE(clean_html, html)
            FROM archive_pages
            WHERE id = ?;
            """,
            (row_id,),
        )
        row = cur.fetchone()
        conn.close()

        if not row:
            QMessageBox.warning(self, "Not found", f"No snapshot with id {row_id}")
            return

        url, html_for_reader = row
        self.browser_pane.load_html_snapshot(html_for_reader, url or "about:blank")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main():
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# Optional: expose DB_PATH for other modules if they want it.
__all__ = ["init_db_if_needed", "DB_PATH"]

