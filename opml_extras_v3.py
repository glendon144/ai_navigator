#!/usr/bin/env python3
# opml_extras_v3.py — Pretty OPML rendering for Qt trees (PiKit/FunKit/AI Navigator)
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

# ---------- Safe text helpers ----------

_XML_BAD = dict.fromkeys(range(0x00, 0x20), None)
# keep TAB(0x09), LF(0x0A), CR(0x0D)
for k in (0x09, 0x0A, 0x0D):
    _XML_BAD.pop(k, None)

def _safe(s: str, max_len: int = 240) -> str:
    if not s:
        return ""
    s = s.translate(_XML_BAD)
    s = " ".join(str(s).split())
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s

# ---------- In-memory model (optional) ----------

@dataclass
class Node:
    text: str
    attrs: Dict[str, str] = field(default_factory=dict)
    kids: List["Node"] = field(default_factory=list)

def _from_el(el: ET.Element) -> Node:
    n = Node(text=_safe(el.attrib.get("text", "(untitled)")),
             attrs={k: _safe(v, 4096) for k, v in el.attrib.items()})
    for ch in el.findall("./outline"):
        n.kids.append(_from_el(ch))
    return n

def load_opml(path: str) -> List[Node]:
    doc = ET.parse(path)
    body = doc.getroot().find("./body")
    if body is None:
        return []
    return [_from_el(el) for el in body.findall("./outline")]

# ---------- Qt rendering ----------

def _new_item(text: str, attrs: Dict[str, str]) -> QTreeWidgetItem:
    item = QTreeWidgetItem([text or "(untitled)"])
    item.setData(0, Qt.UserRole, attrs)
    # Visual cue if node is “openable” locally
    if attrs.get("_local_id"):
        item.setForeground(0, Qt.darkCyan)
    return item

def _attach(node: Node, parent: Optional[QTreeWidgetItem], tree: QTreeWidget):
    item = _new_item(node.text, node.attrs)
    if parent is None:
        tree.addTopLevelItem(item)
    else:
        parent.addChild(item)
    for k in node.kids:
        _attach(k, item, tree)

def populate_qtree_from_opml(tree: QTreeWidget, path: str, *, expand_depth: int = 1):
    """Replace tree contents with OPML from `path` (pretty, collapsible)."""
    tree.clear()
    try:
        roots = load_opml(path)
    except Exception as e:
        tree.addTopLevelItem(QTreeWidgetItem([f"(failed to load OPML: {e})"]))
        return

    if not roots:
        tree.addTopLevelItem(QTreeWidgetItem(["(empty outline)"]))
        return

    for n in roots:
        _attach(n, None, tree)
    tree.expandToDepth(max(0, expand_depth))

def expand_all(tree: QTreeWidget):
    tree.expandAll()

def collapse_all(tree: QTreeWidget):
    tree.collapseAll()

