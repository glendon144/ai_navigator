#!/usr/bin/env python3
#
# ai_navigator.py
#
# AI Navigator prototype (Archive + Recover + Reader Mode + OPML Outline + Reload)
#
# Layout:
#   [ BrowserPane | ResultsPane | OutlinePane | AssistantPane ]
#
# Capabilities:
#   - Archive: capture current page into SQLite (raw + Reader Mode clean_html).
#   - Recover: load a stored snapshot into the browser offline.
#   - Outline: browse archive_export.opml as a clickable knowledge tree.
#       Clicking a node with _local_id pulls that snapshot from SQLite
#       and renders it offline in BrowserPane.
#   - Reload Outline: re-parse archive_export.opml without restarting.
#
# You are now browsing history, not the feed.

import sys
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

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
from init_db import init_db_if_needed

init_db_if_needed()

DB_PATH = Path("search_time_machine.db")
DEFAULT_OPML_PATH = "archive_export.opml"


def ensure_archive_table(db_path: Path):
    """
    Make sure the archive_pages table exists, and migrate it if needed to add clean_html.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Base table
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

    # Migration safety: old DBs might not have clean_html yet.
    try:
        cur.execute("ALTER TABLE archive_pages ADD COLUMN clean_html TEXT;")
    except sqlite3.OperationalError:
        # Column already exists, which is fine.
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

    The goal is a stable, self-contained document that's still readable
    years from now, and doesn't try to phone home or throw paywall overlays.
    This is the canonical body we plan to expose to outlines / PiKit.
    """
    cleaned = re.sub(
        r"<script.*?</script>",
        "",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<iframe.*?</iframe>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<link[^>]+rel=[\"']?(preload|dns-prefetch|preconnect|modulepreload)[\"']?[^>]*>",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # remove inline handlers like onclick="..." onmouseover="..." etc.
    cleaned = re.sub(
        r"\son\w+\s*=\s*['\"].*?['\"]",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
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


class ThrobberWidget(QWidget):
    """
    Rotating "A" throbber for AI Navigator.

    start() -> spins
    stop()  -> freezes

    Internals:
    We draw an "A" in a teal/navy circle once to a QPixmap. Then we just
    rotate that pixmap in paintEvent() using a QTransform, driven by a QTimer.
    """

    def __init__(self, parent=None, size=24):
        super().__init__(parent)
        self.setFixedSize(QSize(size, size))
        self.angle = 0

        self.timer = QTimer(self)
        self.timer.setInterval(50)  # ~20 FPS
        self.timer.timeout.connect(self._tick)

        self.base_pixmap = self._make_base_pixmap(size)

    def _make_base_pixmap(self, size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Background circle: deep teal/navy ringed with light border
        circle_color = QColor(0, 60, 90)
        painter.setBrush(QBrush(circle_color))
        painter.setPen(QPen(QColor(200, 230, 255), 1))
        painter.drawEllipse(QRect(1, 1, size - 2, size - 2))

        # Stylized "A"
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

        # Crossbar of the "A"
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


class BrowserPane(QWidget):
    """
    Left pane:
      Toolbar (Back, Forward, Reload, Home, URL, Go, Archive, Throbber)
      QWebEngineView
      Status line

    load_html_snapshot(html, base_url) lets us display archived snapshots
    offline in Reader Mode.

    We talk to MainWindow via callbacks:
      on_page_loaded(url_str)
      on_archive_request(url, title, html)
    """

    def __init__(self, on_page_loaded=None, on_archive_request=None):
        super().__init__()

        self.on_page_loaded = on_page_loaded
        self.on_archive_request = on_archive_request

        # Core widgets
        self.view = QWebEngineView()

        self.url_bar = QLineEdit()
        self.go_button = QPushButton("Go")
        self.back_button = QPushButton("←")
        self.fwd_button = QPushButton("→")
        self.reload_button = QPushButton("Reload")
        self.home_button = QPushButton("Home")
        self.archive_button = QPushButton("Archive")
        self.throbber = ThrobberWidget(size=24)

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(
            "font-size: 11px; color: #d0e8ff; background-color: #003c5a; padding: 2px;"
        )
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Toolbar row
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
        ):
            b.setStyleSheet(btn_style)

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
        toolbar_row.addWidget(self.throbber)

        # Status row
        status_row = QHBoxLayout()
        status_bg = QWidget()
        status_bg.setStyleSheet(
            "background-color: #003c5a; border-top: 1px solid #99ccee;"
        )
        status_bg.setLayout(status_row)
        status_row.addWidget(self.status_label)

        # Main layout (vertical)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar_bg)
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(status_bg)
        self.setLayout(layout)

        # Default homepage
        self.home_url = "https://www.google.com/"

        # Hook up controls
        self.go_button.clicked.connect(self.load_url)
        self.url_bar.returnPressed.connect(self.load_url)
        self.back_button.clicked.connect(self.view.back)
        self.fwd_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self.view.reload)
        self.home_button.clicked.connect(self.load_home)
        self.archive_button.clicked.connect(self._archive_current_page)

        # Web load signals
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self._on_load_progress)
        self.view.loadFinished.connect(self._on_load_finished)

        # Initial page
        self.url_bar.setText(self.home_url)
        self.load_url()

    def load_home(self):
        self.url_bar.setText(self.home_url)
        self.load_url()

    def load_url(self):
        url = self.url_bar.text().strip()
        if not url.startswith("http"):
            url = "https://" + url
        self.view.setUrl(url)

    def load_html_snapshot(self, html: str, base_url: str):
        """
        Render an archived snapshot (Reader Mode) with no live network requirement.
        base_url seeds the origin so relative paths don't completely freak out.
        """
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
        if ok:
            current_url = self.view.url().toString()
            self.url_bar.setText(current_url)
            self.status_label.setText("Done.")
            if self.on_page_loaded:
                self.on_page_loaded(current_url)
        else:
            self.status_label.setText("Load failed.")
            QMessageBox.warning(self, "Load error", "Page failed to load.")

    def _archive_current_page(self):
        """
        Ask QWebEngineView for the current DOM HTML and hand it to MainWindow.
        """
        current_url = self.view.url().toString()
        current_title = self.view.title() or current_url

        def got_html(html_str):
            if self.on_archive_request:
                self.on_archive_request(current_url, current_title, html_str)

        self.view.page().toHtml(got_html)


class ResultsPane(QWidget):
    """
    Snapshot list pane.

    Top: list of archived snapshots (title + timestamp).
    Bottom: detail box (URL + snippet preview).
    Button: Recover (loads snapshot back into BrowserPane in Reader Mode).

    recoveredPage(html, url) is emitted when user hits Recover.
    """

    recoveredPage = Signal(str, str)  # html, url

    def __init__(self, db_path: Path):
        super().__init__()

        self.db_path = db_path
        self.conn = None

        # widgets
        self.archive_list = QListWidget()
        self.details_list = QListWidget()
        self.recover_button = QPushButton("Recover")

        # chrome labels
        header_label = QLabel("Archived Pages")
        header_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )
        details_label = QLabel("Details")
        details_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )

        # style button
        self.recover_button.setStyleSheet(
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

        # layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(header_label)
        layout.addWidget(self.archive_list, stretch=1)

        # row with "Details" label and Recover button on same line
        details_header_row = QHBoxLayout()
        details_header_row.addWidget(details_label)
        details_header_row.addStretch(1)
        details_header_row.addWidget(self.recover_button)

        layout.addLayout(details_header_row)
        layout.addWidget(self.details_list, stretch=2)

        self.setLayout(layout)

        # signals
        self.archive_list.currentItemChanged.connect(self._populate_details_for_archive)
        self.recover_button.clicked.connect(self._recover_selected)

        # init db + load list
        self._ensure_connection()
        self._populate_archive_list()

    def _ensure_connection(self):
        if self.conn is None:
            ensure_archive_table(self.db_path)
            self.conn = sqlite3.connect(self.db_path)

    def _populate_archive_list(self):
        """
        Fill archive_list from archive_pages, newest first.
        """
        self.archive_list.clear()
        if self.conn is None:
            return

        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, title, captured_at
            FROM archive_pages
            ORDER BY captured_at DESC
            LIMIT 100;
            """
        )
        rows = cur.fetchall()
        for page_id, title, captured_at in rows:
            label = f"{title}    ({captured_at})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, page_id)
            self.archive_list.addItem(item)

    def _populate_details_for_archive(
        self,
        current: QListWidgetItem,
        previous: QListWidgetItem,
    ):
        """
        When user clicks an archived page, show URL + snippet.
        """
        self.details_list.clear()
        if self.conn is None or current is None:
            return

        page_id = current.data(Qt.UserRole)
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT url, snippet
            FROM archive_pages
            WHERE id = ?;
            """,
            (page_id,),
        )
        row = cur.fetchone()
        if not row:
            return

        url, snippet = row
        preview_text = f"{url}\n\n{snippet}"
        self.details_list.addItem(QListWidgetItem(preview_text))

    def _recover_selected(self):
        """
        User hit Recover.
        Look up clean_html (Reader Mode) + URL for the selected snapshot,
        then emit recoveredPage(html, url).
        Falls back to raw html if clean_html is NULL.
        """
        if self.conn is None:
            QMessageBox.warning(self, "No DB", "Database not available.")
            return

        current_item = self.archive_list.currentItem()
        if current_item is None:
            QMessageBox.information(
                self,
                "No selection",
                "Select an archived page first.",
            )
            return

        page_id = current_item.data(Qt.UserRole)
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT url,
                   COALESCE(clean_html, html)
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

    def refresh_all(self):
        """
        Call this after inserting a new archive row.
        """
        if self.conn is None:
            self._ensure_connection()
        self._populate_archive_list()
        # details_list will update on selection; we don't auto-select.


class OutlinePane(QWidget):
    """
    OPML Outline browser.

    Loads archive_export.opml (newest-first outline of your captured web),
    shows it as a tree. Clicking a node that has _local_id will open the
    corresponding snapshot (Reader Mode) directly in BrowserPane.

    Now also includes a 'Reload' button so we can re-parse a freshly
    exported OPML without restarting AI Navigator.
    """

    def __init__(
        self, db_path: Path, on_open_local=None, opml_path: str = DEFAULT_OPML_PATH
    ):
        super().__init__()

        self.db_path = db_path
        self.on_open_local = on_open_local  # callback(local_id:int)
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

        # populate once
        self._populate_tree_from_opml()

        # react to user actions
        self.tree.itemActivated.connect(self._handle_activate)
        self.reload_button.clicked.connect(self.reload_outline)

    def _populate_tree_from_opml(self):
        """
        Clear current tree and repopulate it from self.opml_path.
        """
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

            # stash metadata for click handling (_local_id, url, etc.)
            item.setData(0, Qt.UserRole, xml_el.attrib)

            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)

            # recurse children
            for child in xml_el.findall("./outline"):
                add_outline_element(child, item)

        for top in body.findall("./outline"):
            add_outline_element(top, None)

        # modest auto-expand so you can browse
        self.tree.expandToDepth(1)

    def reload_outline(self):
        """
        Public method: re-parse archive_export.opml, refresh the tree display.
        Intended to be called after you regenerate the OPML externally
        using aopmlengine.py.
        """
        self._populate_tree_from_opml()

    def _handle_activate(self, item, column):
        """
        When the user activates a tree node, check if that outline node
        had a _local_id attribute. If so, ask MainWindow to open it.
        """
        attrs = item.data(0, Qt.UserRole) or {}
        local_id = attrs.get("_local_id")
        if local_id and self.on_open_local:
            try:
                self.on_open_local(int(local_id))
            except ValueError:
                pass  # if somehow it's non-numeric we just ignore


class AssistantPane(QWidget):
    """
    Assistant / commentary pane.

    Eventually this becomes:
    - diff between two captures of the same URL
    - propaganda / rhetoric analysis
    - summary for future readers
    """

    def __init__(self):
        super().__init__()

        self.input_line = QLineEdit()
        self.ask_button = QPushButton("Ask")
        self.output_box = QTextEdit()
        self.output_box.setReadOnly(True)

        guide_label = QLabel("Navigator Guide")
        guide_label.setStyleSheet(
            "font-weight: bold; background-color: #003c5a; color: #ffffff; padding: 4px;"
        )

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Ask:"))
        top_row.addWidget(self.input_line)
        top_row.addWidget(self.ask_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(guide_label)
        layout.addLayout(top_row)
        layout.addWidget(QLabel("Assistant Response:"))
        layout.addWidget(self.output_box, stretch=1)
        self.setLayout(layout)

        self.ask_button.clicked.connect(self.handle_ask)
        self.input_line.returnPressed.connect(self.handle_ask)

    def handle_ask(self):
        question = self.input_line.text().strip()
        if not question:
            return

        response_text = (
            "AI Navigator says:\n\n"
            "The Outline pane is now live-updating. You can regenerate the OPML "
            "export from your archive, hit Reload, and keep exploring that "
            "snapshot web without restarting.\n\n"
            "Soon: pick two captures of the same URL and I'll diff them so you "
            "can watch the narrative mutate over time."
        )

        self.output_box.setPlainText(response_text)


class MainWindow(QWidget):
    """
    Top-level container.

    4-pane layout:
      BrowserPane | ResultsPane | OutlinePane | AssistantPane

    Responsibilities:
      - Save a page into SQLite (Archive).
      - Recover a snapshot from ResultsPane.
      - Open a snapshot by _local_id from the OPML OutlinePane.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("AI Navigator")
        self.setMinimumSize(QSize(1600, 900))

        # Panes
        self.results_pane = ResultsPane(DB_PATH)
        self.assistant_pane = AssistantPane()
        self.browser_pane = BrowserPane(
            on_page_loaded=self._handle_page_loaded,
            on_archive_request=self._handle_archive_request,
        )
        self.outline_pane = OutlinePane(
            DB_PATH,
            on_open_local=self._open_local_snapshot_by_id,
            opml_path=DEFAULT_OPML_PATH,
        )

        # When user hits Recover in ResultsPane, replay snapshot in BrowserPane.
        self.results_pane.recoveredPage.connect(self._handle_recovered_page)

        # Layout:
        # outer_splitter: [ browser_pane | mid_splitter ]
        # mid_splitter:   [ results_pane | outline_pane | assistant_pane ]

        mid_splitter = QSplitter(Qt.Horizontal)
        mid_splitter.addWidget(self.results_pane)
        mid_splitter.addWidget(self.outline_pane)
        mid_splitter.addWidget(self.assistant_pane)
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
        """
        Hook point: eventually auto-archive search result pages,
        diff snapshots over time, etc.
        """
        pass

    def _handle_archive_request(self, url: str, title: str, html: str):
        """
        BrowserPane called Archive.
        Persist snapshot (with clean_html), then refresh the ResultsPane list
        so it appears immediately.
        """
        save_archive_page(DB_PATH, url, title, html)
        self.results_pane.refresh_all()
        # After you rerun aopmlengine.py to export a new archive_export.opml,
        # hit "Reload" in OutlinePane to see the new stuff.

    def _handle_recovered_page(self, html: str, url: str):
        """
        ResultsPane emitted recoveredPage(html, url) from Recover button.
        Push that cleaned HTML into the browser pane for offline viewing.
        """
        self.browser_pane.load_html_snapshot(html, url)

    def _open_local_snapshot_by_id(self, row_id: int):
        """
        OutlinePane asked us to open a specific archived snapshot ID.
        Look up clean_html (fallback html) from SQLite, then show it.
        """
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
            QMessageBox.warning(
                self,
                "Not found",
                f"No snapshot with id {row_id}",
            )
            return

        url, html_for_reader = row
        self.browser_pane.load_html_snapshot(html_for_reader, url or "about:blank")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
