from __future__ import annotations
from typing import Optional, List, Tuple
from PySide6.QtCore import Signal, QDateTime, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QInputDialog, QMessageBox
)
from weave_store import WeaveStore, WeaveItem

class MemoryWeavePane(QWidget):
    # (weave_id, url)
    reweaveRequested = Signal(int, str)
    # ask host to capture current page
    requestCaptureCurrent = Signal()

    def __init__(self, store: WeaveStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.store = store
        self._build_ui()
        self._load()

    # ----- UI -----
    def _build_ui(self):
        self.setObjectName("MemoryWeavePane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header row
        hdr = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter by title, URL, tags, or summary…")
        self.search.returnPressed.connect(self._on_search)
        self.btn_refresh = QPushButton("Refresh", self)
        self.btn_refresh.clicked.connect(self._load)
        hdr.addWidget(self.search, 1)
        hdr.addWidget(self.btn_refresh, 0)

        # Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "When", "Title", "URL", "Summary"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._fire_reweave)

        # Footer row (buttons)
        ftr = QHBoxLayout()
        self.btn_capture = QPushButton("Capture Current", self)
        self.btn_capture.clicked.connect(lambda: self.requestCaptureCurrent.emit())
        self.btn_edit = QPushButton("Edit…", self)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_reweave = QPushButton("Reweave", self)
        self.btn_reweave.clicked.connect(self._fire_reweave)
        ftr.addStretch(1)
        ftr.addWidget(self.btn_capture)
        ftr.addWidget(self.btn_edit)
        ftr.addWidget(self.btn_reweave)

        layout.addLayout(hdr)
        layout.addWidget(self.table, 1)
        layout.addLayout(ftr)

    # ----- Data ops -----
    def _on_search(self):
        q = self.search.text().strip()
        items = self.store.search(q) if q else self.store.list_recent()
        self._populate(items)

    def _load(self):
        self._populate(self.store.list_recent())

    def _populate(self, items: List[WeaveItem]):
        self.table.setRowCount(len(items))
        for row, it in enumerate(items):
            when = QDateTime.fromSecsSinceEpoch(it.captured_at).toString("yyyy-MM-dd HH:mm")
            self.table.setItem(row, 0, QTableWidgetItem(str(it.id)))
            self.table.setItem(row, 1, QTableWidgetItem(when))
            self.table.setItem(row, 2, QTableWidgetItem(it.title))
            self.table.setItem(row, 3, QTableWidgetItem(it.url))
            self.table.setItem(row, 4, QTableWidgetItem(it.summary))
        self.table.resizeColumnsToContents()

    def _selected_weave(self) -> Optional[Tuple[int, str]]:
        model = self.table.selectionModel()
        if not model or not model.selectedRows():
            return None
        r = model.selectedRows()[0].row()
        weave_id = int(self.table.item(r, 0).text())
        url = self.table.item(r, 3).text()
        return weave_id, url

    # ----- Actions -----
    def _fire_reweave(self):
        sel = self._selected_weave()
        if not sel:
            return
        weave_id, url = sel
        self.reweaveRequested.emit(weave_id, url)

    def _edit_selected(self):
        sel = self._selected_weave()
        if not sel:
            QMessageBox.information(self, "No selection", "Select an entry to edit.")
            return
        weave_id, _url = sel

        # Fetch existing item
        item = self.store.get(weave_id)
        if item is None:
            QMessageBox.warning(self, "Not found", f"Weave id {weave_id} not found.")
            return

        # Title
        new_title, ok = QInputDialog.getText(self, "Edit title", "Title:", text=item.title or "")
        if not ok:
            return

        # Summary (multiline)
        new_summary, ok = QInputDialog.getMultiLineText(self, "Edit summary", "Summary:", text=item.summary or "")
        if not ok:
            return

        # Tags (comma-separated)
        new_tags, ok = QInputDialog.getText(self, "Edit tags", "Comma-separated tags:", text=item.tags or "")
        if not ok:
            return

        # Persist
        self.store.update(weave_id, title=new_title, summary=new_summary, tags=new_tags)
        # Refresh view (respect current filter)
        self._on_search() if self.search.text().strip() else self._load()
