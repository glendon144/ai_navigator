#!/usr/bin/env python3
#
# ai_navigator.py
#
# AI Navigator prototype (Archive + Recover + Reader Mode + Recover-to-ChatGPT + Recover Memory Weave + Memory Pane)
#
# Layout:
#   [ BrowserPane | ResultsPane | MemoryPane | GmailPane | WebMCPActionsPane ]
#
# Capabilities:
#   - Archive: capture current page into SQLite (raw + Reader Mode clean_html).
#   - Recover: load a stored snapshot into the browser offline.
#   - Recover to ChatGPT: copy a compact Context Capsule for the selected snapshot
#                         and open chatgpt.com for a paste-and-go resume.
#   - Recover Memory Weave: copy a 3-item recent thread (prefer same domain) and open chatgpt.com.
#   - Memory Pane: separate memory.db that logs URL/title/html on each page load.
#   - Gmail Pane: run gmail_janitor.py dry-runs and approved archive/spam actions.
#
# You are now browsing history, not the feed.

import sys
import re
import os
import json
import html
import importlib.util
import webbrowser
import subprocess
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urljoin
import threading
import time

from PySide6.QtCore import (
    Qt,
    QSize,
    QTimer,
    QRect,
    QUrl,
    QProcess,
    QProcessEnvironment,
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
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QCheckBox,
    QStackedWidget,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

try:
    from PySide6.QtWebEngineCore import QWebEnginePermission
except ImportError:
    QWebEnginePermission = None
from bs4 import BeautifulSoup  # for OPML export parsing

# Initializes storage/ and ensures archive_pages exists (same schema used below).
from init_db import init_db_if_needed
from capture_store import (
    DreamCapture,
    build_handoff,
    ensure_capture_columns,
    save_capture,
)

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
QT_WEBENGINE_SETHTML_LIMIT_BYTES = 2 * 1024 * 1024
WEBMCP_RELAY_ROOT = Path(
    os.getenv(
        "WEBMCP_RELAY_ROOT",
        str(Path(__file__).resolve().parent.parent / "webmcp_relay"),
    )
).expanduser()
WEBMCP_RELAY_SERVER = str(
    Path(
        os.getenv("WEBMCP_RELAY_SERVER", str(WEBMCP_RELAY_ROOT / "server.py"))
    ).expanduser()
)
WEBMCP_CLIENT_MODULE = str(
    Path(
        os.getenv(
            "WEBMCP_CLIENT_MODULE",
            str(WEBMCP_RELAY_ROOT / "webmcp_relay_client" / "webmcp_client.py"),
        )
    ).expanduser()
)
WEBMCP_FLASK_BASE_URL = os.getenv("WEBMCP_FLASK_BASE_URL", "http://127.0.0.1:5054")
APP_DIR = Path(__file__).resolve().parent
SUITE_DIR = APP_DIR.parent
PIKIT_ROOT = Path(os.getenv("PIKIT_ROOT", str(SUITE_DIR / "PiKit-main"))).expanduser()
FUNKIT_ROOT = Path(
    os.getenv("FUNKIT_ROOT", str(SUITE_DIR / "funkit-main"))
).expanduser()
GMAIL_JANITOR_SCRIPT = Path(
    os.getenv("GMAIL_JANITOR_SCRIPT", str(APP_DIR / "gmail_janitor.py"))
).expanduser()

APP_CHROME_STYLESHEET = """
QWidget {
    background: #f3efe6;
    color: #251d13;
    font-size: 13px;
}
QFrame#productSidebar {
    background: #d9d2c3;
    border-right: 1px solid #9f947e;
}
QWidget#workspacePane {
    background: #fbf8f1;
    border: 1px solid #cbbda8;
    border-radius: 10px;
}
QLabel#productSidebarBrand {
    color: #30281d;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 4px 2px 10px 2px;
}
QLabel#productSidebarHint {
    color: #665845;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 0 2px 8px 2px;
}
QPushButton#productSidebarButton {
    text-align: left;
    padding: 12px 14px;
    border: 1px solid #8a7c65;
    border-radius: 0;
    background: #efe8d9;
    color: #241d14;
    font-size: 14px;
    font-weight: 600;
}
QPushButton#productSidebarButton:hover {
    background: #f8f2e5;
}
QPushButton#productSidebarButton:checked {
    background: #fffaf0;
    border-right: 4px solid #2f5da8;
    padding-right: 11px;
}
QLabel#sectionTitle {
    color: #183a5a;
    font-size: 15px;
    font-weight: 700;
    padding: 0;
}
QLabel#sectionSubtitle {
    color: #786a59;
    font-size: 11px;
    padding: 0 0 6px 0;
}
QWidget#toolbarSurface {
    background: #183a5a;
    color: white;
    border: 1px solid #274c71;
    border-radius: 10px 10px 0 0;
}
QWidget#statusSurface {
    background: #183a5a;
    border: 1px solid #274c71;
    border-top: 0;
    border-radius: 0 0 10px 10px;
}
QLabel#statusLabel {
    color: #e6f1ff;
    padding: 4px 8px;
}
QLineEdit,
QComboBox,
QTextEdit,
QListWidget,
QTreeWidget {
    background: #fffdf8;
    border: 1px solid #cabda9;
    border-radius: 8px;
    color: #241d14;
    selection-background-color: #2f5da8;
    selection-color: white;
}
QTextEdit,
QListWidget,
QTreeWidget {
    alternate-background-color: #f6f0e4;
}
QComboBox,
QLineEdit {
    padding: 6px 8px;
}
QPushButton[buttonRole="nav"] {
    background: #214d74;
    color: #ffffff;
    border: 1px solid #9ec3e2;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}
QPushButton[buttonRole="primary"] {
    background: #2f5da8;
    color: #ffffff;
    border: 1px solid #234884;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}
QPushButton[buttonRole="secondary"] {
    background: #e8decb;
    color: #241d14;
    border: 1px solid #bcae96;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}
QPushButton[buttonRole="danger"] {
    background: #874f2d;
    color: #ffffff;
    border: 1px solid #6c3e21;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}
QPushButton:hover {
    filter: brightness(1.04);
}
QSplitter::handle {
    background: #d7cbb7;
    width: 6px;
    height: 6px;
}
QSplitter::handle:hover {
    background: #c3b396;
}
"""

VPN_UI_SUPPORTED = sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# Archive DB helpers (archive_pages)
# ---------------------------------------------------------------------------


def _set_button_role(button: QPushButton, role: str) -> None:
    button.setProperty("buttonRole", role)


def _set_section_title(label: QLabel, subtitle: str | None = None) -> QLabel:
    label.setObjectName("sectionTitle")
    if subtitle is not None:
        label.setToolTip(subtitle)
    return label


def _make_section_subtitle(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionSubtitle")
    label.setWordWrap(True)
    return label


class EnvKeyPromptDialog(QDialog):
    def __init__(self, env_key: str, product_name: str, parent=None):
        super().__init__(parent)
        self.env_key = env_key
        self.setWindowTitle(f"{product_name} Setup")
        self.setMinimumWidth(620)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(f"{product_name} requires {env_key}")
        title.setObjectName("sectionTitle")

        help_text = QLabel(
            f"{env_key} is not set in the environment.\n"
            "Paste the key below, or choose a file that contains just the key."
        )
        help_text.setWordWrap(True)

        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText(f"Paste {env_key} here")
        self.value_edit.setEchoMode(QLineEdit.Password)

        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(8)
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("Optional: choose a key file")
        self.file_path_edit.setReadOnly(True)
        browse_button = QPushButton("Choose File…")
        _set_button_role(browse_button, "secondary")
        browse_button.clicked.connect(self._browse_for_key_file)
        picker_row.addWidget(self.file_path_edit, 1)
        picker_row.addWidget(browse_button)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        ok_button = button_box.button(QDialogButtonBox.Ok)
        cancel_button = button_box.button(QDialogButtonBox.Cancel)
        if ok_button is not None:
            _set_button_role(ok_button, "primary")
        if cancel_button is not None:
            _set_button_role(cancel_button, "secondary")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(title)
        layout.addWidget(help_text)
        layout.addWidget(self.value_edit)
        layout.addLayout(picker_row)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def _browse_for_key_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Key File",
            str(Path.home()),
            "All Files (*)",
        )
        if not filename:
            return
        self.file_path_edit.setText(filename)
        try:
            value = Path(filename).read_text(encoding="utf-8").strip()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Could not read key file",
                f"Failed to read {filename}\n\n{exc}",
            )
            return
        self.value_edit.setText(value)

    def key_value(self) -> str:
        return self.value_edit.text().strip()


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


def _load_webmcp_client_class():
    client_path = Path(WEBMCP_CLIENT_MODULE)
    if not client_path.exists():
        return None

    module_name = "webmcp_relay_client_webmcp_client"
    spec = importlib.util.spec_from_file_location(module_name, client_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, "WebMCPClient", None)


def webmcp_debug_log(event: str, payload=None) -> None:
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if payload is None:
        print(f"[{ts}] [webmcp] {event}", file=sys.stderr, flush=True)
        return
    try:
        rendered = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        rendered = repr(payload)
    print(f"[{ts}] [webmcp] {event}: {rendered}", file=sys.stderr, flush=True)


def _webmcp_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "action"


def _extract_webmcp_json_actions(html: str, base_url: str) -> list[dict]:
    """Extract actions from <script type="application/webmcp+json"> blocks."""
    actions: list[dict] = []
    pattern = re.compile(
        r"<script[^>]*type=[\"']application/webmcp\+json[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:
            webmcp_debug_log("page_miner.json_parse_error", {"error": str(exc)})
            continue
        candidates = payload.get("actions", payload if isinstance(payload, list) else [])
        if not isinstance(candidates, list):
            continue
        for action in candidates:
            if not isinstance(action, dict):
                continue
            name = str(action.get("name") or action.get("action") or "").strip()
            selector = str(action.get("selector") or "").strip()
            if not name or not selector:
                continue
            item = dict(action)
            item.setdefault("name", name)
            item.setdefault("method", "click")
            item.setdefault("risk", item.get("risk_category") or "navigation")
            item.setdefault("risk_category", item.get("risk") or "navigation")
            item.setdefault("homepage", base_url)
            item.setdefault("source", "current_page_manifest")
            if item.get("url"):
                item["url"] = urljoin(base_url, str(item.get("url")))
            actions.append(item)
    return actions


def mine_webmcp_actions_from_html(html: str, base_url: str = "") -> list[dict]:
    """Mine simple WebMCP navigation actions from the currently displayed HTML.

    Supported page hooks:
      - <script type="application/webmcp+json">{"actions": [...]}</script>
      - Elements with data-mcp, data-mcp-action, data-mcp-url,
        data-mcp-description, and data-mcp-risk attributes.
    """
    html = html or ""
    base_url = base_url or ""
    actions = _extract_webmcp_json_actions(html, base_url)
    seen = {str(a.get("name")) for a in actions if a.get("name")}

    tag_pattern = re.compile(r"<(?P<tag>a|button|input)\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
    attr_pattern = re.compile(r"([:\w-]+)\s*=\s*(['\"])(.*?)\2", re.IGNORECASE | re.DOTALL)

    for match in tag_pattern.finditer(html):
        attrs_raw = match.group("attrs") or ""
        attrs = {k.lower(): v for k, _q, v in attr_pattern.findall(attrs_raw)}
        mcp_id = (attrs.get("data-mcp") or "").strip()
        if not mcp_id:
            continue
        label = (attrs.get("value") or attrs.get("aria-label") or attrs.get("title") or mcp_id).strip()
        name = (attrs.get("data-mcp-action") or f"open_{_webmcp_slug(label or mcp_id)}").strip()
        if not name or name in seen:
            continue
        url = (attrs.get("data-mcp-url") or attrs.get("href") or "").strip()
        description = (attrs.get("data-mcp-description") or f"Open {label}.").strip()
        risk = (attrs.get("data-mcp-risk") or "navigation").strip()
        action = {
            "name": name,
            "description": description,
            "method": "click",
            "selector": f"[data-mcp='{mcp_id}']",
            "risk": risk,
            "risk_category": risk,
            "safety_note": "Navigation actions are user-visible only and default to instruction-only responses.",
            "homepage": base_url,
            "source": "current_page_dom",
        }
        if url:
            action["url"] = urljoin(base_url, url)
        actions.append(action)
        seen.add(name)

    return actions


class WebMCPRelayAdapter:
    def __init__(self, source_mode: str = "stdio"):
        self.source_mode = source_mode
        self._client_class = _load_webmcp_client_class()

    def set_source_mode(self, source_mode: str):
        self.source_mode = source_mode

    def status(self) -> dict:
        if self.source_mode == "flask":
            payload = self._http_get("/api/status")
            return payload.get("status", payload)
        return self._client_or_stdio_call("webmcp_manifest_status", {})

    def list_actions(self) -> list[dict]:
        if self.source_mode == "flask":
            payload = self._http_get("/api/actions")
            return payload.get("actions", []) if payload.get("ok") else []
        payload = self._client_or_stdio_call("webmcp_list_actions", {})
        actions = payload.get("actions")
        return actions if isinstance(actions, list) else []

    def get_action(self, action_name: str) -> dict:
        if self.source_mode == "flask":
            return self._http_get(f"/api/action/{action_name}")
        return self._client_or_stdio_call(
            "webmcp_get_action", {"action_name": action_name}
        )

    def normalize_action_name(self, action) -> str:
        if isinstance(action, str):
            return action.strip()
        if isinstance(action, dict):
            for key in ("name", "action", "tool_name"):
                value = action.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def call_action(self, action, parameters: dict | None = None) -> dict:
        action_name = self.normalize_action_name(action)
        params = parameters if isinstance(parameters, dict) else {}
        if not action_name:
            response = {"ok": False, "error": "No valid WebMCP action selected"}
            webmcp_debug_log(
                "adapter.call_action.invalid_selection",
                {"selected_action": action, "parameters": params, "response": response},
            )
            return response
        webmcp_debug_log(
            "adapter.call_action.request",
            {
                "source_mode": self.source_mode,
                "selected_action": action,
                "action_name": action_name,
                "parameters": params,
            },
        )
        if self.source_mode == "flask":
            response = self._http_post(
                "/api/call", {"name": action_name, "parameters": params}
            )
            webmcp_debug_log("adapter.call_action.response", response)
            return response
        response = self._client_or_stdio_call(
            "webmcp_call_action",
            {"action_name": action_name, "parameters": params},
        )
        webmcp_debug_log("adapter.call_action.response", response)
        return response

    def _client_or_stdio_call(self, tool_name: str, arguments: dict) -> dict:
        if tool_name in {"webmcp_get_action", "webmcp_call_action"}:
            if tool_name == "webmcp_call_action":
                action_name = self.normalize_action_name(
                    arguments.get("action_name")
                    or arguments.get("name")
                    or arguments.get("action")
                    or arguments.get("tool_name")
                )
                if not action_name:
                    return {"ok": False, "error": "No valid WebMCP action selected"}
                return self._stdio_rpc(
                    "webmcp_call_action",
                    {
                        "action_name": action_name,
                        "parameters": arguments.get("parameters", {}),
                    },
                )
            action_name = self.normalize_action_name(
                arguments.get("action_name")
                or arguments.get("name")
                or arguments.get("action")
                or arguments.get("tool_name")
            )
            if not action_name:
                return {"ok": False, "error": "No valid WebMCP action selected"}
            return self._stdio_rpc("webmcp_get_action", {"action_name": action_name})

        if self._client_class is not None:
            try:
                client = self._client_class(
                    mode="stdio",
                    manifest_url=os.getenv(
                        "WEBMCP_MANIFEST_URL",
                        "http://jazz.clonesvr.com/.well-known/mcp.json",
                    ),
                    relay_server=WEBMCP_RELAY_SERVER,
                )
                if tool_name == "webmcp_manifest_status":
                    return client.status()
                if tool_name == "webmcp_list_actions":
                    return {"ok": True, "actions": client.list_actions()}
            except Exception as exc:
                return {"ok": False, "error": str(exc), "source_mode": "stdio"}
        return self._stdio_rpc(tool_name, arguments)

    def _stdio_rpc(self, tool_name: str, arguments: dict) -> dict:
        server_path = Path(WEBMCP_RELAY_SERVER)
        if not server_path.exists():
            return {"ok": False, "error": f"Relay server not found: {server_path}"}

        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ai_navigator", "version": "0.2.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        ]
        input_text = "\n".join(json.dumps(msg) for msg in messages) + "\n"

        try:
            proc = subprocess.run(
                [sys.executable, str(server_path)],
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
                cwd=str(server_path.parent),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Relay server timed out."}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": f"Relay server exited with {proc.returncode}: {proc.stderr[-500:]}",
            }

        responses = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                responses.append(json.loads(line))
            except ValueError:
                continue

        final = next((r for r in responses if r.get("id") == 2), {})
        if "error" in final:
            return {"ok": False, "error": final["error"]}

        result = final.get("result", {})
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content:
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except ValueError:
                    return {"ok": True, "text": text}
        return result if isinstance(result, dict) else {"ok": True, "result": result}

    def _http_get(self, path: str) -> dict:
        try:
            import requests

            response = requests.get(WEBMCP_FLASK_BASE_URL + path, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source_mode": "flask"}

    def _http_post(self, path: str, payload: dict) -> dict:
        try:
            import requests

            response = requests.post(
                WEBMCP_FLASK_BASE_URL + path, json=payload, timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source_mode": "flask"}


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

    def _systemctl_available(self) -> bool:
        import platform
        import shutil

        return platform.system() == "Linux" and shutil.which("systemctl") is not None

    def is_active(self) -> bool:
        if not self._systemctl_available():
            return False

        r = self._run("systemctl", "is-active", "--quiet", self.unit)
        return r.returncode == 0

    def start(self) -> bool:
        if not self._systemctl_available():
            return False

        self._run("systemctl", "start", self.unit)
        return self.is_active()

    def stop(self) -> bool:
        if not self._systemctl_available():
            return True

        self._run("systemctl", "stop", self.unit)
        return not self.is_active()

    def _default_route_iface(self) -> str | None:
        import platform
        import shutil

        if platform.system() != "Linux" or shutil.which("ip") is None:
            return None

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
        import platform
        import shutil

        if platform.system() != "Linux" or shutil.which("ip") is None:
            return False

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


def _parse_html_document(html_text: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html_text or "", "lxml")
    except Exception:
        return BeautifulSoup(html_text or "", "html.parser")


def _html_to_opml(html_text: str, title: str) -> str:
    soup = _parse_html_document(html_text or "")
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
        f"<title>{html.escape(doc_title)}</title>",
        "</head><body>",
    ]

    stack = [0]
    for level, text in nodes:
        while stack and level <= stack[-1]:
            out.append("</outline>")
            stack.pop()
        out.append(f'<outline text="{html.escape(text, quote=True)}">')
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


class DiagnosticWebEnginePage(QWebEnginePage):
    """Mirror Chromium JavaScript console messages to the launching Terminal."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(
            f"[web-console] level={level} source={source_id}:{line_number} {message}",
            file=sys.stderr,
            flush=True,
        )
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class BrowserPane(QWidget):
    """
    Left pane:
      Toolbar (Back, Forward, Reload, Home, URL, Go, Archive, OPML export, VPN, Throbber)
      QWebEngineView
      Status line

    New: on_memory_log callback for Memory Pane.
    """

    browserFocusRequested = Signal()

    def __init__(
        self,
        on_page_loaded=None,
        on_archive_request=None,
        on_memory_log=None,
        on_capture_request=None,
    ):
        super().__init__()

        self.on_page_loaded = on_page_loaded
        self.on_archive_request = on_archive_request
        self.on_memory_log = on_memory_log
        self.on_capture_request = on_capture_request

        self.view = QWebEngineView()
        self.view.setPage(DiagnosticWebEnginePage(self.view))
        self._pending_webmcp_execution = None
        self._media_permission_api = "none"
        self.setObjectName("workspacePane")

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Enter a URL or host name")
        self.url_bar.setMinimumWidth(420)
        self.go_button = QPushButton("Go")
        self.back_button = QPushButton("Back")
        self.fwd_button = QPushButton("Forward")
        self.reload_button = QPushButton("Reload")
        self.home_button = QPushButton("Home")
        self.archive_button = QPushButton("Archive")
        self.capture_page_button = QPushButton("Capture Page")
        self.capture_selection_button = QPushButton("Capture Selection")
        self.opml_button = QPushButton("Export Outline")
        self.browser_focus_button = QPushButton("Focus Browser")
        self.mic_test_button = QPushButton("Test Microphone")
        self.throbber = ThrobberWidget(size=24)

        # --- VPN UI ---
        self.vpn = VPNController()
        self.require_vpn = False
        self.vpn_button = None
        self.vpn_status = None
        if VPN_UI_SUPPORTED:
            self.vpn_button = QPushButton("VPN")
            self.vpn_button.setCheckable(True)
            self.vpn_status = QLabel("●")

        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        toolbar_layout = QVBoxLayout()
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)
        utility_row = QHBoxLayout()
        utility_row.setContentsMargins(0, 0, 0, 0)
        utility_row.setSpacing(8)
        toolbar_bg = QWidget()
        toolbar_bg.setObjectName("toolbarSurface")
        toolbar_bg.setLayout(toolbar_layout)
        for b in (
            self.back_button,
            self.fwd_button,
            self.reload_button,
            self.home_button,
            self.go_button,
        ):
            _set_button_role(b, "nav")
        for b in (
            self.archive_button,
            self.capture_page_button,
            self.capture_selection_button,
            self.opml_button,
            self.browser_focus_button,
            self.mic_test_button,
        ):
            _set_button_role(b, "secondary")
        if self.vpn_button is not None:
            _set_button_role(self.vpn_button, "secondary")
        if self.vpn_status is not None:
            self.vpn_status.setStyleSheet("color: red; padding-left:6px;")

        brand_label = QLabel("AI Navigator", parent=toolbar_bg)
        brand_label.setObjectName("sectionTitle")

        nav_row.addWidget(brand_label)
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.fwd_button)
        nav_row.addWidget(self.reload_button)
        nav_row.addWidget(self.home_button)
        nav_row.addWidget(QLabel("Address", parent=toolbar_bg))
        nav_row.addWidget(self.url_bar, stretch=1)
        nav_row.addWidget(self.go_button)

        utility_row.addWidget(self.archive_button)
        utility_row.addWidget(self.capture_page_button)
        utility_row.addWidget(self.capture_selection_button)
        utility_row.addWidget(self.opml_button)
        utility_row.addWidget(self.browser_focus_button)
        utility_row.addWidget(self.mic_test_button)
        utility_row.addStretch(1)
        if self.vpn_button is not None:
            utility_row.addWidget(self.vpn_button)
        if self.vpn_status is not None:
            utility_row.addWidget(self.vpn_status)
        utility_row.addWidget(self.throbber)
        toolbar_layout.addLayout(nav_row)
        toolbar_layout.addLayout(utility_row)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(6, 4, 6, 6)
        status_bg = QWidget()
        status_bg.setObjectName("statusSurface")
        status_bg.setLayout(status_row)
        status_row.addWidget(self.status_label)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)
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
        self.capture_page_button.clicked.connect(self._capture_current_page)
        self.capture_selection_button.clicked.connect(self._capture_selection)
        self.opml_button.clicked.connect(self._export_outline_opml)
        self.browser_focus_button.clicked.connect(self.browserFocusRequested.emit)
        self.mic_test_button.clicked.connect(self._run_microphone_diagnostics)

        self._install_media_permission_handler()
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self._on_load_progress)
        self.view.loadFinished.connect(self._on_load_finished)

        self.vpn_timer = None
        if self.vpn_button is not None:
            self.vpn_button.toggled.connect(self._toggle_vpn)
            self.vpn_timer = QTimer(self)
            self.vpn_timer.timeout.connect(self._refresh_vpn_status)
            self.vpn_timer.start(1500)

        self.url_bar.setText(self.home_url)
        self.load_url()

    def set_browser_focus_active(self, active: bool):
        self.browser_focus_button.setText("Restore Layout" if active else "Focus Browser")

    def _install_media_permission_handler(self):
        """Install the best microphone/camera permission API available."""
        page = self.view.page()
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(self._on_permission_requested)
            self._media_permission_api = "QWebEnginePermission (Qt 6.8+)"
        elif hasattr(page, "featurePermissionRequested"):
            page.featurePermissionRequested.connect(
                self._on_legacy_feature_permission_requested
            )
            self._media_permission_api = "QWebEnginePage legacy feature API"
        print(
            f"[media] permission API: {self._media_permission_api}; "
            f"platform={sys.platform}; executable={sys.executable}",
            file=sys.stderr,
            flush=True,
        )

    @staticmethod
    def _permission_name(value) -> str:
        return getattr(value, "name", str(value))

    @staticmethod
    def _trusted_media_origin(origin: str) -> bool:
        host = (urlparse(origin).hostname or "").lower()
        return (
            host == "chatgpt.com"
            or host.endswith(".chatgpt.com")
            or host == "openai.com"
            or host.endswith(".openai.com")
            or host in {"localhost", "127.0.0.1", "::1"}
        )

    def _on_permission_requested(self, permission):
        origin = permission.origin().toString()
        permission_name = self._permission_name(permission.permissionType())
        print(
            f"[media] permission requested: origin={origin} type={permission_name} "
            f"state={self._permission_name(permission.state())}",
            file=sys.stderr,
            flush=True,
        )
        media_names = {
            "MediaAudioCapture",
            "MediaVideoCapture",
            "MediaAudioVideoCapture",
        }
        if permission_name not in media_names:
            permission.deny()
            return
        trusted = self._trusted_media_origin(origin)
        answer = QMessageBox.question(
            self,
            "Microphone / camera permission",
            f"Allow {origin} to use {permission_name}?\n\n"
            f"Qt permission API: {self._media_permission_api}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes if trusted else QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            permission.grant()
            self.status_label.setText(f"Granted {permission_name} to {origin}")
            print(f"[media] granted {permission_name} to {origin}", file=sys.stderr, flush=True)
        else:
            permission.deny()
            self.status_label.setText(f"Denied {permission_name} to {origin}")
            print(f"[media] denied {permission_name} to {origin}", file=sys.stderr, flush=True)

    def _on_legacy_feature_permission_requested(self, security_origin, feature):
        origin = security_origin.toString()
        feature_name = self._permission_name(feature)
        print(
            f"[media] legacy permission requested: origin={origin} feature={feature_name}",
            file=sys.stderr,
            flush=True,
        )
        media_features = {
            QWebEnginePage.MediaAudioCapture,
            QWebEnginePage.MediaVideoCapture,
            QWebEnginePage.MediaAudioVideoCapture,
        }
        if feature not in media_features:
            policy = QWebEnginePage.PermissionDeniedByUser
        else:
            trusted = self._trusted_media_origin(origin)
            answer = QMessageBox.question(
                self,
                "Microphone / camera permission",
                f"Allow {origin} to use {feature_name}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes if trusted else QMessageBox.No,
            )
            policy = (
                QWebEnginePage.PermissionGrantedByUser
                if answer == QMessageBox.Yes
                else QWebEnginePage.PermissionDeniedByUser
            )
        self.view.page().setFeaturePermission(security_origin, feature, policy)
        print(f"[media] legacy decision: {feature_name} -> {policy}", file=sys.stderr, flush=True)

    def _run_microphone_diagnostics(self):
        """Exercise enumerateDevices/getUserMedia and show the exact result."""
        script = r'''(async () => {
  const report = {
    href: location.href,
    secureContext: window.isSecureContext,
    userAgent: navigator.userAgent,
    mediaDevicesPresent: !!navigator.mediaDevices,
    getUserMediaPresent: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    permissionState: null,
    devicesBefore: [], devicesAfter: [], success: false,
    errorName: null, errorMessage: null
  };
  try {
    if (navigator.permissions && navigator.permissions.query) {
      try {
        const p = await navigator.permissions.query({name: 'microphone'});
        report.permissionState = p.state;
      } catch (e) {
        report.permissionState = 'query-not-supported: ' + e.name;
      }
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
      throw new Error('navigator.mediaDevices.getUserMedia is unavailable');
    report.devicesBefore = (await navigator.mediaDevices.enumerateDevices()).map(d => ({
      kind: d.kind, label: d.label, deviceId: d.deviceId ? '[present]' : '[empty]'
    }));
    const stream = await navigator.mediaDevices.getUserMedia({audio: true, video: false});
    report.success = true;
    report.trackSettings = stream.getAudioTracks().map(t => ({
      label: t.label, enabled: t.enabled, muted: t.muted,
      readyState: t.readyState, settings: t.getSettings()
    }));
    report.devicesAfter = (await navigator.mediaDevices.enumerateDevices()).map(d => ({
      kind: d.kind, label: d.label, deviceId: d.deviceId ? '[present]' : '[empty]'
    }));
    stream.getTracks().forEach(t => t.stop());
  } catch (e) {
    report.errorName = e && e.name ? e.name : 'Error';
    report.errorMessage = e && e.message ? e.message : String(e);
  }
  console.log('[AI Navigator microphone diagnostic]', report);
  return JSON.stringify(report, null, 2);
})();'''
        self.status_label.setText("Testing microphone access…")

        def _show_report(result):
            text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
            print(f"[media] diagnostic result:\n{text}", file=sys.stderr, flush=True)
            try:
                report = json.loads(text)
            except Exception:
                report = {}
            success = bool(report.get("success"))
            self.status_label.setText(
                "Microphone test succeeded." if success else "Microphone test failed; see report."
            )
            box = QMessageBox(self)
            box.setWindowTitle("Microphone diagnostic")
            box.setIcon(QMessageBox.Information if success else QMessageBox.Warning)
            box.setText(
                "The browser opened and read an audio stream."
                if success
                else f"Microphone access failed: {report.get('errorName')}: {report.get('errorMessage')}"
            )
            box.setDetailedText(text)
            box.exec()

        self.view.page().runJavaScript(script, _show_report)

    # VPN helpers
    def _toggle_vpn(self, checked: bool):
        if not VPN_UI_SUPPORTED:
            return
        self.require_vpn = checked
        if checked:
            threading.Thread(target=self._bring_vpn_up, daemon=True).start()
        else:
            self.vpn.stop()
            self._refresh_vpn_status()

    def _bring_vpn_up(self):
        if not VPN_UI_SUPPORTED:
            return
        ok = self.vpn.ensure_connected(timeout_s=25)
        self.status_label.setText("VPN connected" if ok else "VPN connection failed")
        self._refresh_vpn_status()

    def _refresh_vpn_status(self):
        if self.vpn_status is None:
            return
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
        html_size = len((html or "").encode("utf-8"))
        if html_size > QT_WEBENGINE_SETHTML_LIMIT_BYTES:
            fd, temp_path = tempfile.mkstemp(
                suffix=".html",
                prefix="ai-navigator-recovered-",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                temp_file.write(html or "")
            if not hasattr(self, "_recovered_snapshot_files"):
                self._recovered_snapshot_files = []
            self._recovered_snapshot_files.append(temp_path)
            local_url = QUrl.fromLocalFile(temp_path)
            print(
                "[Recover] load_html_snapshot: using temp file "
                f"html_size={html_size} limit={QT_WEBENGINE_SETHTML_LIMIT_BYTES} "
                f"base_url={base_url} temp_path={temp_path}",
                flush=True,
            )
            self.view.setUrl(local_url)
            self.status_label.setText(
                "Loaded large Reader-Mode snapshot from temp file."
            )
        else:
            print(
                "[Recover] load_html_snapshot: using setHtml "
                f"html_size={html_size} limit={QT_WEBENGINE_SETHTML_LIMIT_BYTES} "
                f"base_url={base_url}",
                flush=True,
            )
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

        pending = self._pending_webmcp_execution
        if pending is not None:
            self._pending_webmcp_execution = None
            pending()

    def load_from_memory(self, url: str):
        """Load a URL initiated from the Memory Pane selection."""
        if not url:
            return
        self.url_bar.setText(url)
        self.load_url()

    def execute_webmcp_action(self, payload: dict, on_complete=None):
        allowed, reason = self._validate_webmcp_execution(payload)
        if not allowed:
            self.status_label.setText(reason)
            if on_complete:
                on_complete(False, {"ok": False, "error": reason})
            return

        homepage = str(payload.get("homepage") or "").strip()
        current_url = self.view.url().toString()

        def run_action():
            self._run_webmcp_js(payload, on_complete)

        if homepage and not current_url.startswith(homepage):
            self.status_label.setText(f"Opening {homepage} for WebMCP action…")
            self._pending_webmcp_execution = run_action
            self.url_bar.setText(homepage)
            self.load_url()
            return

        run_action()

    def _validate_webmcp_execution(self, payload: dict) -> tuple[bool, str]:
        if not isinstance(payload, dict) or not payload.get("ok", False):
            return False, "WebMCP action payload is not executable."

        method = str(payload.get("method") or "")
        action_name = str(payload.get("action") or "")
        selector = str(payload.get("selector") or "")

        if method not in {"click", "setValueAndChange"}:
            return False, f"Unsupported WebMCP method: {method}"
        if not selector:
            return False, "WebMCP action is missing a selector."
        if method == "click" and not (
            action_name.startswith(("open_", "visit_", "view_"))
            or "navigation" in action_name
        ):
            return (
                False,
                "Only visible click-navigation actions are executable in the webview.",
            )
        if method == "setValueAndChange" and "value" not in payload:
            params = payload.get("parameters") or {}
            if "value" not in params:
                return False, "Parameterized navigation action is missing a value."
        return True, "ok"

    def _run_webmcp_js(self, payload: dict, on_complete=None):
        method = payload.get("method")
        selector = json.dumps(str(payload.get("selector") or ""))
        params = payload.get("parameters") or {}
        value = payload.get("value", params.get("value", ""))

        if method == "click":
            script = f"""
(() => {{
  const el = document.querySelector({selector});
  if (!el) return {{ok:false, error:'selector_not_found'}};
  el.scrollIntoView({{behavior:'auto', block:'center', inline:'center'}});
  const rect = el.getBoundingClientRect();
  const visible = rect.width > 0 && rect.height > 0;
  if (!visible) return {{ok:false, error:'element_not_visible'}};
  el.click();
  return {{ok:true, method:'click', selector:{selector}, text:(el.innerText || el.textContent || '').trim().slice(0, 200)}};
}})();
"""
        else:
            value_json = json.dumps(value)
            script = f"""
(() => {{
  const el = document.querySelector({selector});
  if (!el) return {{ok:false, error:'selector_not_found'}};
  el.scrollIntoView({{behavior:'auto', block:'center', inline:'center'}});
  const rect = el.getBoundingClientRect();
  const visible = rect.width > 0 && rect.height > 0;
  if (!visible) return {{ok:false, error:'element_not_visible'}};
  el.focus();
  el.value = {value_json};
  el.dispatchEvent(new Event('input', {{bubbles:true}}));
  el.dispatchEvent(new Event('change', {{bubbles:true}}));
  return {{ok:true, method:'setValueAndChange', selector:{selector}, value:el.value}};
}})();
"""

        def _done(result):
            ok = bool(isinstance(result, dict) and result.get("ok"))
            if ok:
                self.status_label.setText(
                    f"Executed WebMCP action: {payload.get('action')}"
                )
            else:
                detail = (
                    result.get("error")
                    if isinstance(result, dict)
                    else "unknown_webview_error"
                )
                self.status_label.setText(f"WebMCP action failed: {detail}")
            if on_complete:
                on_complete(ok, result)

        self.view.page().runJavaScript(script, _done)

    def _archive_current_page(self):
        current_url = self.view.url().toString()
        current_title = self.view.title() or current_url

        def got_html(html_str):
            if self.on_archive_request:
                self.on_archive_request(current_url, current_title, html_str)

        self.view.page().toHtml(got_html)

    def _capture_current_page(self):
        self._capture(selection_text="")

    def _capture_selection(self):
        selection = self.view.page().selectedText().strip()
        if not selection:
            QMessageBox.information(
                self,
                "Nothing selected",
                "Select some text in the page, then choose Capture Selection.",
            )
            return
        self._capture(selection_text=selection)

    def _capture(self, selection_text: str):
        current_url = self.view.url().toString()
        current_title = self.view.title() or current_url

        def got_html(html_str):
            if not self.on_capture_request:
                return
            capture = self.on_capture_request(
                current_url, current_title, html_str, selection_text
            )
            kind = "selection" if selection_text else "page"
            self.status_label.setText(
                f"Dream Capture saved ({kind}) — {capture.title}"
            )

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
    handoffRequested = Signal(str, str)  # destination, clipboard packet

    def __init__(self, db_path: Path):
        super().__init__()
        self.setObjectName("workspacePane")

        self.db_path = db_path
        self.conn = None

        self.archive_list = QListWidget()
        self.archive_list.setAlternatingRowColors(True)
        self.details_list = QListWidget()
        self.details_list.setAlternatingRowColors(True)

        self.recover_button = QPushButton("Recover")
        self.recover_chat_button = QPushButton("Recover to ChatGPT")
        self.recover_weave_button = QPushButton("Recover Memory Weave")
        self.pikit_button = QPushButton("Organize in PiKit")
        self.funkit_button = QPushButton("Ask FunKit")

        header_label = QLabel("Archived Pages")
        _set_section_title(header_label)
        subtitle_label = _make_section_subtitle(
            "Recovered pages, snippets, and clipboard handoff tools."
        )
        details_label = QLabel("Details")
        _set_section_title(details_label)
        for b in (
            self.recover_button,
            self.recover_chat_button,
            self.recover_weave_button,
            self.pikit_button,
            self.funkit_button,
        ):
            _set_button_role(b, "primary")

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(header_label)
        layout.addWidget(subtitle_label)
        layout.addWidget(self.archive_list, stretch=1)

        details_header_row = QHBoxLayout()
        details_header_row.addWidget(details_label)
        details_header_row.addStretch(1)
        details_header_row.addWidget(self.pikit_button)
        details_header_row.addWidget(self.funkit_button)
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
        self.pikit_button.clicked.connect(lambda: self._handoff_selected("PiKit"))
        self.funkit_button.clicked.connect(lambda: self._handoff_selected("FunKit"))

        self._ensure_connection()
        self._populate_archive_list()

    def _ensure_connection(self):
        if self.conn is None:
            ensure_archive_table(self.db_path)
            ensure_capture_columns(self.db_path)
            self.conn = sqlite3.connect(self.db_path)

    def _populate_archive_list(self):
        self.archive_list.clear()
        if self.conn is None:
            return
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, title, captured_at, capture_type
            FROM archive_pages
            ORDER BY captured_at DESC
            LIMIT 200;
            """
        )
        for page_id, title, captured_at, capture_type in cur.fetchall():
            marker = "Selection" if capture_type == "selection" else "Page"
            label = f"[{marker}] {title}    ({captured_at})"
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
            """
            SELECT url, snippet, summary, tags, note
            FROM archive_pages WHERE id = ?;
            """,
            (page_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        url, snippet, summary, tags, note = row
        preview_text = (
            f"{url}\n\nSummary\n{summary or snippet}\n\n"
            f"Tags\n{tags or '(none)'}"
        )
        if note:
            preview_text += f"\n\nNote\n{note}"
        self.details_list.addItem(QListWidgetItem(preview_text))

    def _selected_capture(self) -> DreamCapture | None:
        item = self.archive_list.currentItem()
        if self.conn is None or item is None:
            return None
        row = self.conn.execute(
            """
            SELECT id, url, title, captured_at, capture_type, selection_text,
                   summary, tags, note
            FROM archive_pages WHERE id = ?
            """,
            (item.data(Qt.UserRole),),
        ).fetchone()
        return DreamCapture(*row) if row else None

    def _handoff_selected(self, destination: str):
        capture = self._selected_capture()
        if capture is None:
            QMessageBox.information(
                self, "No selection", "Select a captured page or selection first."
            )
            return
        packet = build_handoff(capture, destination)
        if not copy_to_clipboard(packet):
            QMessageBox.warning(self, "Clipboard problem", "Could not copy the capture.")
            return
        self.handoffRequested.emit(destination, packet)

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
        self.setObjectName("workspacePane")

        self.db_path = db_path
        self.on_open_local = on_open_local
        self.opml_path = opml_path

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)

        self.reload_button = QPushButton("Reload")
        _set_button_role(self.reload_button, "secondary")

        header_label = QLabel("Outline (OPML export)")
        _set_section_title(header_label)

        header_row = QHBoxLayout()
        header_row.addWidget(header_label)
        header_row.addStretch(1)
        header_row.addWidget(self.reload_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
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
        self.setObjectName("workspacePane")
        self.db_path = db_path

        # Header
        header_label = QLabel("Memory Tree")
        _set_section_title(header_label)
        subtitle_label = _make_section_subtitle(
            "Recent sessions grouped by hour, domain, then page."
        )

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        _set_button_role(self.refresh_button, "secondary")

        # Layout
        header_row = QHBoxLayout()
        header_row.addWidget(header_label)
        header_row.addStretch(1)
        header_row.addWidget(self.refresh_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header_row)
        layout.addWidget(subtitle_label)
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


class GmailPane(QWidget):
    """
    Gmail janitor control pane.

    This pane deliberately shells out to gmail_janitor.py instead of importing it.
    That keeps OAuth, Gmail API handling, and mailbox mutation in one audited tool.

    Safety model:
      - Dry-run buttons never pass --do-it.
      - Live buttons ask for explicit confirmation before passing --do-it.
      - No permanent delete is exposed here.
    """

    commandFinished = Signal(str)

    def __init__(self, janitor_script: Path = GMAIL_JANITOR_SCRIPT, browser_pane=None):
        super().__init__()
        self.setObjectName("workspacePane")
        self.janitor_script = Path(janitor_script).expanduser()
        self.browser_pane = browser_pane
        self._running = False

        header_label = QLabel("Gmail Janitor")
        _set_section_title(header_label)
        subtitle_label = _make_section_subtitle(
            "Dry-run mailbox cleanup first, then confirm live changes."
        )

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(
            "Gmail query, e.g. from:(@example.com) older_than:30d"
        )
        self.query_edit.setText("from:(@alignpromptfundssolutionsflagship.com)")

        self.message_id_edit = QLineEdit()
        self.message_id_edit.setPlaceholderText(
            "Current Gmail message/thread id, optional"
        )
        self.message_id_edit.setToolTip(
            "When set, Gmail Janitor acts on this specific Gmail message id instead of using the search query."
        )

        self.use_current_button = QPushButton("Use Open Gmail Message")
        self.use_current_button.setToolTip(
            "Best effort: if the browser pane is showing an open Gmail message, extract its Gmail id and fill the target field."
        )

        self.max_edit = QLineEdit()
        self.max_edit.setPlaceholderText("Max")
        self.max_edit.setText("50")
        self.max_edit.setMaximumWidth(60)

        self.include_spam_trash = QCheckBox("Search Spam/Trash too")
        self.include_spam_trash.setChecked(True)
        self.include_spam_trash.setToolTip(
            "Pass --include-spam-trash to gmail_janitor.py so searches include messages already in Spam or Trash."
        )

        self.dry_archive_button = QPushButton("Dry Run Archive")
        self.live_archive_button = QPushButton("Archive in Gmail")
        self.dry_spam_trash_button = QPushButton("Dry Run Spam/Trash")
        self.live_spam_trash_button = QPushButton("Spam+Trash")

        self.output_view = QTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setPlaceholderText(
            "gmail_janitor.py output will appear here. Dry-runs are safe and change nothing. "
            "The Search Spam/Trash option maps to --include-spam-trash."
        )

        self.status_label = QLabel(f"Script: {self.janitor_script}")
        self.status_label.setWordWrap(True)

        for button in (
            self.use_current_button,
            self.dry_archive_button,
            self.dry_spam_trash_button,
        ):
            _set_button_role(button, "secondary")
        for button in (self.live_archive_button, self.live_spam_trash_button):
            _set_button_role(button, "danger")

        top_row = QHBoxLayout()
        top_row.addWidget(header_label)
        top_row.addStretch(1)

        query_row = QHBoxLayout()
        query_row.addWidget(QLabel("Query:"))
        query_row.addWidget(self.query_edit, stretch=1)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Current ID:"))
        target_row.addWidget(self.message_id_edit, stretch=1)
        target_row.addWidget(self.use_current_button)

        option_row = QHBoxLayout()
        option_row.addWidget(QLabel("Max:"))
        option_row.addWidget(self.max_edit)
        option_row.addWidget(self.include_spam_trash)
        option_row.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.dry_archive_button)
        button_row.addWidget(self.live_archive_button)
        button_row.addWidget(self.dry_spam_trash_button)
        button_row.addWidget(self.live_spam_trash_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(top_row)
        layout.addWidget(subtitle_label)
        layout.addLayout(query_row)
        layout.addLayout(target_row)
        layout.addLayout(option_row)
        layout.addLayout(button_row)
        layout.addWidget(self.output_view, stretch=1)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.use_current_button.clicked.connect(self.use_open_gmail_message)
        self.dry_archive_button.clicked.connect(
            lambda: self.run_janitor("archive", do_it=False)
        )
        self.live_archive_button.clicked.connect(
            lambda: self.run_janitor("archive", do_it=True)
        )
        self.dry_spam_trash_button.clicked.connect(
            lambda: self.run_janitor("spam-trash", do_it=False)
        )
        self.live_spam_trash_button.clicked.connect(
            lambda: self.run_janitor("spam-trash", do_it=True)
        )
        self.commandFinished.connect(self._handle_command_finished)

    def _set_buttons_enabled(self, enabled: bool):
        for button in (
            self.use_current_button,
            self.dry_archive_button,
            self.live_archive_button,
            self.dry_spam_trash_button,
            self.live_spam_trash_button,
        ):
            button.setEnabled(enabled)

    def _build_command(self, action: str, do_it: bool) -> list[str]:
        query = self.query_edit.text().strip()
        message_id = self.message_id_edit.text().strip()

        if not query and not message_id:
            raise ValueError("Enter a Gmail query or use/open a Gmail message first.")

        try:
            max_count = int(self.max_edit.text().strip() or "50")
        except ValueError:
            raise ValueError("Max must be a number.")

        if max_count < 1:
            raise ValueError("Max must be at least 1.")
        if max_count > 500:
            raise ValueError("Max is capped at 500 from the GUI for safety.")

        if action not in {"archive", "spam-trash"}:
            raise ValueError(f"Unsupported Gmail action: {action}")

        cmd = [
            sys.executable,
            str(self.janitor_script),
        ]

        if message_id:
            cmd.extend(["--message-id", message_id])
        else:
            cmd.extend(["--query", query])

        cmd.extend(
            [
                f"--{action}",
                "--max",
                str(max_count),
            ]
        )

        if self.include_spam_trash.isChecked() and not message_id:
            cmd.append("--include-spam-trash")

        if do_it:
            cmd.append("--do-it")

        return cmd

    def use_open_gmail_message(self):
        """
        Best-effort bridge from the browser pane to the Gmail API layer.

        Gmail's web UI is not a stable API, so this method is deliberately
        defensive. It first verifies that the browser pane is on mail.google.com,
        then asks the page for a JSON string containing the current URL, sender,
        and subject. Returning JSON text avoids PySide/QWebEngine object-marshalling
        surprises that can otherwise show up as "Unknown extraction failure."

        If a usable Gmail id is visible, we fill Current ID. If not, we build a
        narrow query from the open sender and subject. Dry-run remains the guardrail.
        """
        if self.browser_pane is None:
            QMessageBox.warning(self, "Gmail Janitor", "No browser pane is connected.")
            return

        current_url = self.browser_pane.view.url().toString()
        if "mail.google.com" not in current_url:
            QMessageBox.warning(
                self,
                "Gmail Janitor",
                "The browser pane is not currently on mail.google.com. Open Gmail in the browser pane first.",
            )
            return

        script = r"""
(() => {
  function finish(payload) {
    try {
      return JSON.stringify(payload || {});
    } catch (e) {
      return JSON.stringify({ok:false, error:"Could not stringify extraction result: " + String(e)});
    }
  }

  try {
    const href = String(location.href || "");
    const host = String(location.hostname || "");
    if (!host.includes("mail.google.com")) {
      return finish({ok:false, error:"The browser pane is not currently on mail.google.com", href});
    }

    function textOf(el) {
      return el ? String(el.innerText || el.textContent || "").trim() : "";
    }

    function pickText(selectors) {
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        const text = textOf(el);
        if (text) return text;
      }
      return "";
    }

    function pickEmail() {
      const selectors = [".gD[email]", "span[email]", "[email]", "[data-hovercard-id]"];
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (!el) continue;
        const candidates = [
          el.getAttribute("email"),
          el.getAttribute("data-hovercard-id"),
          el.getAttribute("aria-label"),
          textOf(el),
          el.getAttribute("title")
        ];
        for (const c of candidates) {
          const m = String(c || "").match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
          if (m) return m[0];
        }
      }
      const bodyText = document.body ? textOf(document.body).slice(0, 20000) : "";
      const m = bodyText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
      return m ? m[0] : "";
    }

    function pickGmailId() {
      const patterns = [
        /[?&#](?:th|message_id)=([A-Za-z0-9_-]{8,})/i,
        /\/(?:inbox|sent|trash|spam|all|important|starred)\/([A-Za-z0-9_-]{8,})(?:[/?#]|$)/i,
        /#(?:inbox|sent|trash|spam|all|important|starred)\/([A-Za-z0-9_-]{8,})/i,
        /\/([a-f0-9]{12,})(?:[/?#]|$)/i
      ];
      for (const re of patterns) {
        const m = href.match(re);
        if (m) return decodeURIComponent(m[1]);
      }
      return "";
    }

    const gmailId = pickGmailId();
    const subject = pickText(["h2.hP", "[data-thread-perm-id]", "h2", "[role='main'] h1"]);
    const fromEmail = pickEmail();
    const fromText = pickText([".gD", "span[email]", "[email]", "[data-hovercard-id]"]);
    const title = String(document.title || "");

    return finish({
      ok: Boolean(gmailId || (fromEmail && subject)),
      gmailId,
      subject,
      fromEmail,
      fromText,
      title,
      href,
      error: "Could not find an open Gmail message id or enough sender/subject text. Open an individual message first."
    });
  } catch (e) {
    return finish({ok:false, error:"Gmail extraction JavaScript failed: " + String(e && (e.stack || e.message) || e)});
  }
})();
"""

        def _done(raw_result):
            result = None
            if isinstance(raw_result, str):
                try:
                    result = json.loads(raw_result)
                except Exception as exc:
                    QMessageBox.warning(
                        self,
                        "Gmail Janitor",
                        "Could not parse Gmail extraction result.\n\n"
                        f"Raw result: {raw_result[:500]}\n\nError: {exc}",
                    )
                    return
            elif isinstance(raw_result, dict):
                result = raw_result
            else:
                QMessageBox.warning(
                    self,
                    "Gmail Janitor",
                    "Gmail extraction returned no usable result.\n\n"
                    f"Current URL: {current_url}\n\n"
                    "Open an individual Gmail message, wait for it to finish loading, then try again.",
                )
                return

            if not result.get("ok"):
                detail = (
                    result.get("error") or "Could not identify the open Gmail message."
                )
                href = result.get("href") or current_url
                QMessageBox.warning(self, "Gmail Janitor", f"{detail}\n\nURL: {href}")
                self.output_view.setPlainText(
                    "Gmail current-message extraction failed.\n\n"
                    f"Reason: {detail}\n"
                    f"URL: {href}\n"
                    f"Document title: {result.get('title', '')}\n"
                    f"From candidate: {result.get('fromEmail', '')}\n"
                    f"Subject candidate: {result.get('subject', '')}\n"
                )
                return

            gmail_id = str(result.get("gmailId") or "").strip()
            subject = str(result.get("subject") or "").strip()
            from_email = str(result.get("fromEmail") or "").strip()
            href = str(result.get("href") or current_url).strip()

            if gmail_id and not gmail_id.startswith("FMfc"):
                self.message_id_edit.setText(gmail_id)
                self.status_label.setText(
                    f"Captured Gmail message id from browser pane: {gmail_id}"
                )
                self.output_view.setPlainText(
                    "Captured current Gmail message from browser pane.\n"
                    f"Message ID: {gmail_id}\n"
                    f"From: {from_email}\n"
                    f"Subject: {subject}\n"
                    f"URL: {href}\n\n"
                    "Dry-run Archive or Dry-run Spam/Trash will now target this ID."
                )
                return

            # Gmail web often exposes an FMfc... thread token that is not always a
            # Gmail API message id. Prefer a narrow query in that case.
            safe_subject = subject.replace('"', '\\"')
            parts = []
            if from_email:
                parts.append(f"from:{from_email}")
            if safe_subject:
                parts.append(f'subject:"{safe_subject}"')
            query = " ".join(parts).strip()
            if query:
                self.query_edit.setText(query)
                self.message_id_edit.clear()
                self.status_label.setText(
                    "Built a narrow Gmail query from the open message."
                )
                self.output_view.setPlainText(
                    "AI Navigator built a narrow Gmail query from the open message.\n"
                    "This is safer than using Gmail's web-only FMfc thread token as an API id.\n\n"
                    f"Query: {query}\n"
                    f"Web token: {gmail_id}\n"
                    f"URL: {href}\n\n"
                    "Use dry-run first to verify the exact message count."
                )
                return

            QMessageBox.warning(
                self,
                "Gmail Janitor",
                "Could not extract a Gmail API id or build a narrow query. Try opening the message in its own Gmail view and wait for it to finish loading.",
            )

        self.browser_pane.view.page().runJavaScript(script, _done)

    def run_janitor(self, action: str, do_it: bool):
        if self._running:
            QMessageBox.information(
                self, "Gmail Janitor", "A Gmail command is already running."
            )
            return

        try:
            cmd = self._build_command(action, do_it)
        except Exception as exc:
            QMessageBox.warning(self, "Gmail Janitor", str(exc))
            return

        if not self.janitor_script.exists():
            QMessageBox.warning(
                self,
                "Gmail Janitor",
                f"gmail_janitor.py was not found:\n{self.janitor_script}\n\n"
                "Set GMAIL_JANITOR_SCRIPT or place the script at ~/gmail_janitor.py.",
            )
            return

        if do_it:
            query = self.query_edit.text().strip()
            message_id = self.message_id_edit.text().strip()
            target = f"Message ID: {message_id}" if message_id else f"Query: {query}"
            ok = QMessageBox.question(
                self,
                "Confirm Gmail change",
                "This will modify Gmail using gmail_janitor.py.\n\n"
                f"Action: {action}\n"
                f"{target}\n\n"
                "Proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ok != QMessageBox.Yes:
                return

        mode = "LIVE" if do_it else "DRY RUN"
        rendered = " ".join(cmd)
        self.output_view.setPlainText(f"Running {mode} command:\n{rendered}\n\n")
        self.status_label.setText("Gmail command running…")
        self._running = True
        self._set_buttons_enabled(False)

        def worker():
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=120,
                    cwd=str(Path.home()),
                )
                output = proc.stdout or ""
                if proc.returncode != 0:
                    output += f"\n[exit status {proc.returncode}]\n"
            except subprocess.TimeoutExpired:
                output = "Gmail command timed out after 120 seconds.\n"
            except Exception as exc:
                output = f"Gmail command failed: {exc}\n"
            self.commandFinished.emit(output)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_command_finished(self, output: str):
        existing = self.output_view.toPlainText()
        self.output_view.setPlainText(existing + output)
        self.status_label.setText("Gmail command complete.")
        self._running = False
        self._set_buttons_enabled(True)


# ---------------------------------------------------------------------------
# Gmail Pane
# ---------------------------------------------------------------------------


class WebMCPActionsPane(QWidget):
    def __init__(self, browser_pane: BrowserPane):
        super().__init__()
        self.setObjectName("workspacePane")
        self.browser_pane = browser_pane
        self.adapter = WebMCPRelayAdapter()
        self.actions = []
        self.selected_action = None
        self.last_payload = None

        header_label = QLabel("WebMCP Actions")
        _set_section_title(header_label)
        subtitle_label = _make_section_subtitle(
            "Mine actions from the current page first. If none are embedded, fall back to the relay catalog."
        )

        self.source_combo = QComboBox()
        self.source_combo.addItem("Relay server (stdio)", "stdio")
        self.source_combo.addItem("Flask relay client", "flask")

        self.refresh_button = QPushButton("Refresh Actions")
        self.inspect_button = QPushButton("Preview Payload")
        self.execute_button = QPushButton("Run In Browser")
        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)

        self.actions_list = QListWidget()
        self.actions_list.setAlternatingRowColors(True)
        self.actions_list.setToolTip(
            "Select an action to inspect its parameters and generated payload."
        )
        self.param_value = QComboBox()
        self.param_value.setEditable(True)
        self.param_value.setInsertPolicy(QComboBox.NoInsert)
        self.param_value.setEnabled(False)
        self.param_value.setToolTip("Used for actions with a single value parameter.")

        self.payload_view = QTextEdit()
        self.payload_view.setReadOnly(True)
        _set_button_role(self.refresh_button, "secondary")
        _set_button_role(self.inspect_button, "secondary")
        _set_button_role(self.execute_button, "primary")

        top_row = QHBoxLayout()
        top_row.addWidget(header_label)
        top_row.addStretch(1)
        top_row.addWidget(self.source_combo)
        top_row.addWidget(self.refresh_button)

        call_row = QHBoxLayout()
        call_row.addWidget(QLabel("Value:"))
        call_row.addWidget(self.param_value, stretch=1)
        call_row.addWidget(self.inspect_button)
        call_row.addWidget(self.execute_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(top_row)
        layout.addWidget(subtitle_label)
        layout.addWidget(self.actions_list, stretch=1)
        layout.addLayout(call_row)
        layout.addWidget(self.payload_view, stretch=2)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.source_combo.currentIndexChanged.connect(self._change_source)
        self.refresh_button.clicked.connect(self.refresh)
        self.inspect_button.clicked.connect(self.inspect_selected_action)
        self.execute_button.clicked.connect(self.execute_selected_action)
        self.actions_list.currentItemChanged.connect(self._handle_selection_change)

        if not Path(WEBMCP_RELAY_SERVER).exists():
            self.status_label.setText(f"WebMCP relay not found:\n{WEBMCP_RELAY_SERVER}")
        else:
            self.refresh()

    def _selected_action_name(self) -> str:
        return self.adapter.normalize_action_name(self.selected_action)

    def _change_source(self):
        self.adapter.set_source_mode(self.source_combo.currentData())
        self.refresh()

    def refresh(self):
        self.actions_list.clear()
        self.actions = []
        self.selected_action = None
        self.last_payload = None
        self.payload_view.clear()
        self.status_label.setText("Mining current page for WebMCP actions…")

        def _got_html(html_str: str):
            current_url = self.browser_pane.view.url().toString()
            page_actions = mine_webmcp_actions_from_html(html_str, current_url)
            if page_actions:
                status = {
                    "ok": True,
                    "title": "Current page",
                    "source": "current_page",
                    "url": current_url,
                    "actions": len(page_actions),
                }
                self._set_actions(
                    page_actions,
                    status,
                    f"Current page: loaded {len(page_actions)} WebMCP actions.",
                )
                return

            actions = self.adapter.list_actions()
            status = self.adapter.status()
            if not actions and status.get("ok") is False:
                self.status_label.setText(
                    str(status.get("error") or "Could not load WebMCP actions.")
                )
                self.payload_view.setPlainText(json.dumps(status, indent=2, sort_keys=True))
                return

            title = status.get("title") or status.get("name") or "WebMCP"
            self._set_actions(
                actions,
                status,
                f"{title}: loaded {len(actions)} actions via {self.adapter.source_mode} fallback.",
            )

        self.browser_pane.view.page().toHtml(_got_html)

    def _set_actions(self, actions: list[dict], status: dict, message: str):
        self.actions_list.clear()
        self.actions = actions if isinstance(actions, list) else []
        self.selected_action = None
        self.last_payload = None

        for action in self.actions:
            label = f"{action.get('name', '(unnamed)')} [{action.get('method', '?')}]"
            risk = action.get("risk") or action.get("risk_category")
            if risk:
                label += f" · {risk}"
            source = action.get("source")
            if source:
                label += f" · {source}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, action)
            self.actions_list.addItem(item)

        self.status_label.setText(message)
        self.payload_view.setPlainText(json.dumps(status, indent=2, sort_keys=True))

    def _handle_selection_change(self, current, previous):
        action = current.data(Qt.UserRole) if current is not None else None
        self.selected_action = action
        self.last_payload = None
        self._configure_parameter_editor(action)
        if action is not None:
            self.payload_view.setPlainText(json.dumps(action, indent=2, sort_keys=True))
            self.status_label.setText(f"Selected WebMCP action: {action.get('name')}")

    def _configure_parameter_editor(self, action: dict | None):
        self.param_value.clear()
        self.param_value.setEnabled(False)
        if not action:
            return
        value_schema = (action.get("parameters") or {}).get("value") or {}
        enum_values = value_schema.get("enum") or []
        if enum_values:
            self.param_value.addItems([str(v) for v in enum_values])
            self.param_value.setCurrentIndex(0)
            self.param_value.setEnabled(True)
            return
        if action.get("method") == "setValueAndChange":
            self.param_value.setEnabled(True)
            if value_schema.get("default") is not None:
                self.param_value.setEditText(str(value_schema.get("default")))
            else:
                self.param_value.setEditText("")

    def _selected_parameters(self) -> dict:
        if not self.selected_action:
            return {}
        if self.selected_action.get("method") != "setValueAndChange":
            return {}
        value = self.param_value.currentText().strip()
        return {"value": value} if value else {}

    def inspect_selected_action(self):
        if not self.selected_action:
            QMessageBox.information(self, "No action", "Select a WebMCP action first.")
            return
        action_name = self._selected_action_name()
        params = self._selected_parameters()
        webmcp_debug_log(
            "pane.inspect_selected_action",
            {
                "selected_action_name": action_name,
                "selected_action": self.selected_action,
                "parameters": params,
            },
        )
        payload = self.adapter.call_action(self.selected_action, params)
        self.last_payload = payload
        webmcp_debug_log("pane.inspect_selected_action.response", payload)
        self.payload_view.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        self.status_label.setText(
            str(
                payload.get("suggested_browser_instruction")
                or payload.get("error")
                or "Instruction JSON updated."
            )
        )

    def execute_selected_action(self):
        if not self.selected_action:
            QMessageBox.information(self, "No action", "Select a WebMCP action first.")
            return
        action_name = self._selected_action_name()
        params = self._selected_parameters()
        webmcp_debug_log(
            "pane.execute_selected_action",
            {
                "selected_action_name": action_name,
                "selected_action": self.selected_action,
                "parameters": params,
                "last_payload": self.last_payload,
            },
        )
        if not action_name:
            error = "No valid WebMCP action selected"
            self.status_label.setText(error)
            QMessageBox.warning(self, "Invalid action", error)
            return
        if (
            not isinstance(self.last_payload, dict)
            or self.last_payload.get("action") != action_name
        ):
            self.inspect_selected_action()
        payload = self.last_payload or {}
        webmcp_debug_log("pane.execute_selected_action.payload", payload)
        if not payload.get("ok"):
            webmcp_debug_log("pane.execute_selected_action.blocked", payload)
            QMessageBox.warning(
                self,
                "Action blocked",
                str(payload.get("error") or "Action is not executable."),
            )
            return

        def _done(ok: bool, result):
            webmcp_debug_log(
                "pane.execute_selected_action.webview_response",
                {"ok": ok, "result": result},
            )
            if isinstance(result, dict):
                merged = dict(payload)
                merged["webview_execution"] = result
                self.payload_view.setPlainText(
                    json.dumps(merged, indent=2, sort_keys=True)
                )
            if ok:
                self.status_label.setText(
                    f"Executed {payload.get('action')} in the current webview."
                )
            else:
                detail = (
                    result.get("error")
                    if isinstance(result, dict)
                    else "unknown_webview_error"
                )
                self.status_label.setText(f"Execution blocked or failed: {detail}")

        self.browser_pane.execute_webmcp_action(payload, _done)

    # ---------------------------------------------------------------------------


# Main Window
# ---------------------------------------------------------------------------


class MainWindow(QWidget):
    """
    5-pane layout:
      BrowserPane | ResultsPane | MemoryPane | GmailPane | WebMCPActionsPane
    """

    productHandoffRequested = Signal(str, str)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("AI Navigator")
        self.setMinimumSize(QSize(1800, 900))

        self.results_pane = ResultsPane(DB_PATH)
        self.memory_pane = MemoryPane(MEMORY_DB_PATH)
        self.browser_pane = BrowserPane(
            on_page_loaded=self._handle_page_loaded,
            on_archive_request=self._handle_archive_request,
            on_memory_log=self._handle_memory_log,
            on_capture_request=self._handle_capture_request,
        )
        self.gmail_pane = GmailPane(browser_pane=self.browser_pane)
        self.webmcp_pane = WebMCPActionsPane(self.browser_pane)
        self._browser_focus_active = False
        self._saved_outer_splitter_sizes = None
        self._saved_mid_splitter_sizes = None
        self._saved_mid_visibility = None

        self.results_pane.recoveredPage.connect(self._handle_recovered_page)
        self.results_pane.handoffRequested.connect(self.productHandoffRequested.emit)
        self.memory_pane.openUrlRequested.connect(self.browser_pane.load_from_memory)
        self.browser_pane.browserFocusRequested.connect(self._toggle_browser_focus)

        self.mid_splitter = QSplitter(Qt.Horizontal)
        self.mid_splitter.addWidget(self.results_pane)
        self.mid_splitter.addWidget(self.memory_pane)
        self.mid_splitter.addWidget(self.gmail_pane)
        self.mid_splitter.addWidget(self.webmcp_pane)
        self.mid_splitter.setSizes([300, 320, 0, 420])
        self.gmail_pane.hide()
        for index in range(self.mid_splitter.count()):
            self.mid_splitter.setCollapsible(index, True)

        self.outer_splitter = QSplitter(Qt.Horizontal)
        self.outer_splitter.addWidget(self.browser_pane)
        self.outer_splitter.addWidget(self.mid_splitter)
        self.outer_splitter.setSizes([900, 700])
        for index in range(self.outer_splitter.count()):
            self.outer_splitter.setCollapsible(index, True)

        self.browser_focus_shortcut = QShortcut(QKeySequence("Ctrl+Shift+B"), self)
        self.browser_focus_shortcut.activated.connect(self._toggle_browser_focus)

        self.menu_bar_row = QWidget()
        self.menu_bar_row.setObjectName("toolbarSurface")
        menu_row_layout = QHBoxLayout()
        menu_row_layout.setContentsMargins(10, 8, 10, 8)
        menu_row_layout.setSpacing(8)
        self.menu_bar_row.setLayout(menu_row_layout)

        menu_label = QLabel("Suite Menu")
        menu_label.setObjectName("sectionTitle")
        menu_row_layout.addWidget(menu_label)

        self.browser_menu_button = QPushButton("Browser")
        self.archive_menu_button = QPushButton("Archive")
        self.memory_menu_button = QPushButton("Memory")
        self.gmail_menu_button = QPushButton("Gmail")
        self.webmcp_menu_button = QPushButton("WebMCP")
        self.restore_menu_button = QPushButton("All Panes")

        for button in (
            self.browser_menu_button,
            self.archive_menu_button,
            self.memory_menu_button,
            self.gmail_menu_button,
            self.webmcp_menu_button,
            self.restore_menu_button,
        ):
            _set_button_role(button, "secondary")
            menu_row_layout.addWidget(button)

        menu_row_layout.addStretch(1)

        self.browser_menu_button.clicked.connect(self._expand_browser_focus)
        self.archive_menu_button.clicked.connect(lambda: self._focus_side_pane(0))
        self.memory_menu_button.clicked.connect(lambda: self._focus_side_pane(1))
        self.gmail_menu_button.clicked.connect(lambda: self._focus_side_pane(2))
        self.webmcp_menu_button.clicked.connect(lambda: self._focus_side_pane(3))
        self.restore_menu_button.clicked.connect(self._restore_default_layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)
        main_layout.addWidget(self.menu_bar_row)
        main_layout.addWidget(self.outer_splitter)
        self.setLayout(main_layout)
        self.outer_splitter.setHandleWidth(8)
        self.mid_splitter.setHandleWidth(8)

    def _toggle_browser_focus(self):
        if self._browser_focus_active:
            self._restore_browser_focus()
        else:
            self._expand_browser_focus()

    def _restore_default_layout(self):
        if self._browser_focus_active:
            self._restore_browser_focus()
        for i in range(self.mid_splitter.count()):
            self.mid_splitter.widget(i).show()
        self.outer_splitter.setSizes([900, 700])
        self.mid_splitter.setSizes([300, 320, 360, 420])

    def _expand_browser_focus(self):
        if self._browser_focus_active:
            return

        self._saved_outer_splitter_sizes = self.outer_splitter.sizes()
        self._saved_mid_splitter_sizes = self.mid_splitter.sizes()
        self._saved_mid_visibility = [
            self.mid_splitter.widget(i).isVisible()
            for i in range(self.mid_splitter.count())
        ]
        browser_width = max(
            self.outer_splitter.width(), sum(self._saved_outer_splitter_sizes), 1
        )

        for i in range(self.mid_splitter.count()):
            self.mid_splitter.widget(i).hide()
        self.mid_splitter.setSizes([0] * self.mid_splitter.count())
        self.outer_splitter.setSizes([browser_width, 0])
        self._browser_focus_active = True
        self.browser_pane.set_browser_focus_active(True)

    def _restore_browser_focus(self):
        if not self._browser_focus_active:
            return

        if self._saved_mid_visibility:
            for i, visible in enumerate(self._saved_mid_visibility):
                self.mid_splitter.widget(i).setVisible(visible)
        if self._saved_mid_splitter_sizes:
            self.mid_splitter.setSizes(self._saved_mid_splitter_sizes)
        if self._saved_outer_splitter_sizes:
            self.outer_splitter.setSizes(self._saved_outer_splitter_sizes)

        self._browser_focus_active = False
        self._saved_outer_splitter_sizes = None
        self._saved_mid_splitter_sizes = None
        self._saved_mid_visibility = None
        self.browser_pane.set_browser_focus_active(False)

    def _focus_side_pane(self, pane_index: int):
        if self._browser_focus_active:
            self._restore_browser_focus()

        for i in range(self.mid_splitter.count()):
            self.mid_splitter.widget(i).setVisible(i == pane_index)

        outer_width = max(self.outer_splitter.width(), 1)
        browser_width = max(int(outer_width * 0.48), 520)
        side_width = max(outer_width - browser_width, 420)
        self.outer_splitter.setSizes([browser_width, side_width])

        sizes = [0] * self.mid_splitter.count()
        if 0 <= pane_index < len(sizes):
            sizes[pane_index] = side_width
        self.mid_splitter.setSizes(sizes)

    def _handle_page_loaded(self, url_str: str):
        # Hook point for future auto-archive/diff logic.
        pass

    def _handle_archive_request(self, url: str, title: str, html: str):
        save_archive_page(DB_PATH, url, title, html)
        self.results_pane.refresh_all()

    def _handle_capture_request(
        self, url: str, title: str, html: str, selection_text: str
    ) -> DreamCapture:
        capture = save_capture(
            DB_PATH,
            url=url,
            title=title,
            page_html=html,
            selection_text=selection_text,
        )
        self.results_pane.refresh_all()
        self._focus_side_pane(0)
        if self.results_pane.archive_list.count():
            self.results_pane.archive_list.setCurrentRow(0)
        return capture

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
# Product launcher panes
# ---------------------------------------------------------------------------


class ProductLauncherPane(QWidget):
    """
    Launches a sibling product without importing its GUI into this Qt process.

    PiKit and FunKit currently use Tk-based entry points, so process launch is
    the clean integration boundary for this stage of the suite shell.
    """

    def __init__(self, title: str, description: str, root_path: Path, venv_name: str):
        super().__init__()
        self.setObjectName("workspacePane")
        self.title = title
        self.root_path = root_path
        self.venv_name = venv_name
        self.process: QProcess | None = None

        layout = QVBoxLayout()
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(16)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")

        body_label = QLabel(
            f"{description}\n\n"
            "This product runs as a sibling application launched from the suite shell.\n\n"
            "PiKit, FunKit, and AI Navigator will later share memory through "
            "the Dream Capsule substrate."
        )
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.path_label = QLabel(f"Checkout: {self.root_path}")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)

        self.launch_button = QPushButton(f"Launch {title}")
        _set_button_role(self.launch_button, "primary")
        self.launch_button.clicked.connect(self.launch_product)

        self.output_view = QTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setMaximumHeight(180)

        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addWidget(self.path_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.launch_button)
        layout.addWidget(self.output_view)
        layout.addStretch(1)
        self.setLayout(layout)

    def _python_executable(self) -> Path:
        suite_python = Path.home() / ".venvs" / "ai_communicator" / "bin" / "python"
        if suite_python.exists():
            return suite_python
        venv_python = Path.home() / ".venvs" / self.venv_name / "bin" / "python"
        if venv_python.exists():
            return venv_python
        return Path(sys.executable)

    def _required_env_keys(self) -> list[str]:
        if self.title == "PiKit":
            return ["BASETEN_API_KEY"]
        if self.title == "FunKit":
            try:
                providers_path = self.root_path / "storage" / "providers.json"
                app_state_path = self.root_path / "storage" / "app_state.json"
                if providers_path.exists():
                    providers_data = json.loads(
                        providers_path.read_text(encoding="utf-8")
                    )
                    selected_provider = providers_data.get("default")
                    if app_state_path.exists():
                        app_state = json.loads(
                            app_state_path.read_text(encoding="utf-8")
                        )
                        selected_provider = (
                            app_state.get("selected_provider") or selected_provider
                        )
                    for provider in providers_data.get("providers", []):
                        if provider.get("key") == selected_provider:
                            env_key = str(provider.get("env_key") or "").strip()
                            return [env_key] if env_key else []
            except Exception:
                pass
        return []

    def _ensure_runtime_dependencies(self, python_exe: Path) -> bool:
        if self.title != "FunKit":
            return True

        try:
            probe = subprocess.run(
                [str(python_exe), "-c", "import PIL"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15,
                cwd=str(self.root_path),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "FunKit Setup",
                f"Could not verify FunKit dependencies.\n\n{exc}",
            )
            return False

        if probe.returncode == 0:
            return True

        choice = QMessageBox.question(
            self,
            "FunKit Needs Pillow",
            "FunKit could not start because the selected environment is missing "
            "`PIL` (the Pillow package).\n\n"
            f"Interpreter: {python_exe}\n\n"
            "Install Pillow into this environment now and continue launching FunKit?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if choice != QMessageBox.Yes:
            self.status_label.setText("FunKit launch canceled: Pillow not installed.")
            self.output_view.append(
                "FunKit requires Pillow (`PIL`). Launch canceled by user."
            )
            return False

        self.status_label.setText("Installing Pillow for FunKit…")
        self.output_view.append(
            f"Installing Pillow into {python_exe.parent.parent} ..."
        )
        try:
            install = subprocess.run(
                [str(python_exe), "-m", "pip", "install", "Pillow"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
                cwd=str(self.root_path),
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "FunKit Setup",
                f"Failed to install Pillow.\n\n{exc}",
            )
            self.status_label.setText("FunKit launch failed: Pillow install error.")
            return False

        output = (install.stdout or "").strip()
        if output:
            self.output_view.append(output)

        if install.returncode != 0:
            QMessageBox.critical(
                self,
                "FunKit Setup",
                "Pillow installation failed.\n\n"
                f"Exit status: {install.returncode}",
            )
            self.status_label.setText("FunKit launch failed: Pillow install failed.")
            return False

        self.status_label.setText("Pillow installed. Continuing FunKit launch…")
        return True

    def _ensure_launch_environment(self) -> QProcessEnvironment | None:
        env = QProcessEnvironment.systemEnvironment()
        for env_key in self._required_env_keys():
            current_value = (os.environ.get(env_key) or "").strip()
            if current_value:
                env.insert(env_key, current_value)
                continue

            dialog = EnvKeyPromptDialog(env_key, self.title, parent=self)
            if dialog.exec() != QDialog.Accepted:
                self.status_label.setText(
                    f"{self.title} launch canceled: missing {env_key}."
                )
                return None

            key_value = dialog.key_value()
            if not key_value:
                QMessageBox.warning(
                    self,
                    f"{self.title} Setup",
                    f"No value was provided for {env_key}.",
                )
                self.status_label.setText(
                    f"{self.title} launch canceled: missing {env_key}."
                )
                return None

            os.environ[env_key] = key_value
            env.insert(env_key, key_value)

        return env

    def launch_product(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            self.status_label.setText(f"{self.title} is already running.")
            return

        entrypoint = self.root_path / "main.py"
        if not entrypoint.exists():
            self.status_label.setText(f"Missing entrypoint: {entrypoint}")
            return

        python_exe = self._python_executable()
        if not python_exe.exists():
            self.status_label.setText(f"Missing Python executable: {python_exe}")
            return

        if not self._ensure_runtime_dependencies(python_exe):
            return

        launch_env = self._ensure_launch_environment()
        if launch_env is None:
            return

        self.output_view.clear()
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.root_path))
        self.process.setProcessEnvironment(launch_env)
        self.process.setProgram(str(python_exe))
        self.process.setArguments([str(entrypoint)])
        self.process.readyReadStandardOutput.connect(self._append_stdout)
        self.process.readyReadStandardError.connect(self._append_stderr)
        self.process.finished.connect(self._handle_finished)
        self.process.errorOccurred.connect(self._handle_error)
        self.process.start()

        self.status_label.setText(f"Launching {self.title} with {python_exe}")

    def _append_stdout(self):
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
            self.output_view.append(text.rstrip())

    def _append_stderr(self):
        if self.process:
            text = bytes(self.process.readAllStandardError()).decode(
                "utf-8", errors="replace"
            )
            self.output_view.append(text.rstrip())

    def _handle_finished(self, exit_code: int, exit_status):
        self.status_label.setText(f"{self.title} exited with code {exit_code}.")

    def _handle_error(self, error):
        self.status_label.setText(
            f"Could not launch {self.title}: {self.process.errorString()}"
        )


# ---------------------------------------------------------------------------
# Suite Shell
# ---------------------------------------------------------------------------


class SuiteShell(QWidget):
    """
    Top-level product shell for the AI Dream Communicator suite.

    The existing AI Navigator surface remains intact and is hosted as the
    default product mode. PiKit and FunKit are placeholders until their product
    panes are ported into this shell.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("AI Dream Communicator")
        self.setMinimumSize(QSize(1800, 900))
        self.product_stack = QStackedWidget()
        self.product_buttons: list[QPushButton] = []

        shell_layout = QHBoxLayout()
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("productSidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(10)

        brand_label = QLabel("Communicator")
        brand_label.setObjectName("productSidebarBrand")
        brand_label.setWordWrap(True)
        sidebar_layout.addWidget(brand_label)

        hint_label = QLabel("Products")
        hint_label.setObjectName("productSidebarHint")
        sidebar_layout.addWidget(hint_label)

        self.ai_navigator = MainWindow()
        self.ai_navigator.productHandoffRequested.connect(self._handle_product_handoff)
        self._add_product_button("AI Navigator", self.ai_navigator, sidebar_layout)
        self._add_product_button(
            "PiKit",
            ProductLauncherPane(
                "PiKit",
                "OPML / knowledge organization mode.",
                PIKIT_ROOT,
                "pikit",
            ),
            sidebar_layout,
        )
        self._add_product_button(
            "FunKit",
            ProductLauncherPane(
                "FunKit",
                "AI query / LLM interaction mode.",
                FUNKIT_ROOT,
                "funkit",
            ),
            sidebar_layout,
        )

        sidebar_layout.addStretch(1)
        sidebar.setLayout(sidebar_layout)
        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(self.product_stack, 1)
        self.setLayout(shell_layout)

        self._apply_shell_styles()
        self._select_product(0)

    def _add_product_button(
        self, label: str, widget: QWidget, sidebar_layout: QVBoxLayout
    ) -> None:
        index = self.product_stack.addWidget(widget)
        button = QPushButton(label)
        button.setCheckable(True)
        button.setObjectName("productSidebarButton")
        button.clicked.connect(lambda checked=False, i=index: self._select_product(i))
        self.product_buttons.append(button)
        sidebar_layout.addWidget(button)

    def _select_product(self, index: int) -> None:
        self.product_stack.setCurrentIndex(index)
        for idx, button in enumerate(self.product_buttons):
            button.setChecked(idx == index)

    def _handle_product_handoff(self, destination: str, packet: str) -> None:
        index = 1 if destination == "PiKit" else 2
        self._select_product(index)
        pane = self.product_stack.widget(index)
        if isinstance(pane, ProductLauncherPane):
            pane.status_label.setText(
                f"Dream Capture copied. Launch {destination}, then paste it to continue."
            )

    def _apply_shell_styles(self) -> None:
        self.setStyleSheet(APP_CHROME_STYLESHEET)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def main():
    existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    diagnostic_flags = "--no-sandbox --enable-logging=stderr --v=1"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        f"{existing_flags} {diagnostic_flags}".strip()
    )
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")
    print(
        "[media] Chromium flags=" + os.environ["QTWEBENGINE_CHROMIUM_FLAGS"],
        file=sys.stderr,
        flush=True,
    )
    print(
        "[media] Remote DevTools: http://127.0.0.1:"
        + os.environ["QTWEBENGINE_REMOTE_DEBUGGING"],
        file=sys.stderr,
        flush=True,
    )
    app = QApplication(sys.argv)
    w = SuiteShell()
    w.show()
    sys.exit(app.exec())


def test_webmcp_adapter(
    action_name: str = "open_kxci", source_mode: str = "stdio"
) -> int:
    adapter = WebMCPRelayAdapter(source_mode=source_mode)
    status = adapter.status()
    actions = adapter.list_actions()
    selected = next(
        (
            action
            for action in actions
            if adapter.normalize_action_name(action) == action_name
        ),
        None,
    )
    params = {}
    response = adapter.call_action(selected or action_name, params)

    webmcp_debug_log("test_webmcp_adapter.status", status)
    webmcp_debug_log("test_webmcp_adapter.actions_count", {"count": len(actions)})
    webmcp_debug_log("test_webmcp_adapter.selected_action", selected)
    webmcp_debug_log(
        "test_webmcp_adapter.call",
        {"action_name": action_name, "parameters": params, "response": response},
    )

    print(
        json.dumps(
            {
                "status": status,
                "actions_count": len(actions),
                "selected_action": selected,
                "call_response": response,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    if "--test-webmcp-adapter" in sys.argv:
        test_index = sys.argv.index("--test-webmcp-adapter")
        action_name = "open_kxci"
        if test_index + 1 < len(sys.argv) and not sys.argv[test_index + 1].startswith(
            "--"
        ):
            action_name = sys.argv[test_index + 1]
        raise SystemExit(test_webmcp_adapter(action_name=action_name))
    main()

# Optional: expose DB_PATH for other modules if they want it.
__all__ = ["init_db_if_needed", "DB_PATH", "MainWindow", "SuiteShell"]
