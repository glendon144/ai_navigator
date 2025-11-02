#!/usr/bin/env bash
set -euo pipefail

FILE="ai_navigator.py"
BACKUP="ai_navigator.py.bak.refresh-$(date +%Y%m%d-%H%M%S)"

[[ -f "$FILE" ]] || { echo "ERROR: $FILE not found"; exit 1; }
cp -v "$FILE" "$BACKUP"

python3 - <<'PY'
import re
from pathlib import Path

p = Path("ai_navigator.py")
s = p.read_text()

# Find ResultsPane class
m_cls = re.search(r"\nclass\s+ResultsPane\s*\([^)]*\)\s*:\s*\n", s)
if not m_cls:
    raise SystemExit("ERROR: class ResultsPane not found.")
cls_start = m_cls.end()
m_next = re.search(r"\nclass\s+\w+\s*\(", s[cls_start:])
cls_end = cls_start + (m_next.start() if m_next else len(s)-cls_start)
body = s[cls_start:cls_end]

# If refresh_all already exists, exit quietly.
if re.search(r"^\s{4}def\s+refresh_all\s*\(", body, re.M):
    print("refresh_all() already exists; no changes.")
    raise SystemExit(0)

# Insert after _populate_archive_list (or at end of class if not found)
m_anchor = re.search(r"^\s{4}def\s+_populate_archive_list\s*\(.*?\):\n(?:(?: {8}|\t).*\n)*", body, re.M)
insert_at = m_anchor.end() if m_anchor else len(body)

method = """
def refresh_all(self):
    # Refresh the archive list and keep the current selection if possible.
    current_row = self.archive_list.currentRow() if self.archive_list.count() > 0 else -1
    self._populate_archive_list()
    count = self.archive_list.count()
    if count == 0:
        self.details_list.clear()
        try:
            self.weave_preview.setHtml("<p><em>No items yet.</em></p>")
        except Exception:
            pass
        return
    if 0 <= current_row < count:
        self.archive_list.setCurrentRow(current_row)
    else:
        self.archive_list.setCurrentRow(0)
"""

# Indent for class scope
indented = "\n" + "\n".join(("    " + ln if ln.strip() else ln) for ln in method.strip("\n").splitlines()) + "\n"
new_body = body[:insert_at] + indented + body[insert_at:]
s = s[:cls_start] + new_body + s[cls_end:]
p.write_text(s)
print("refresh_all() added to ResultsPane.")
PY

echo "Done. Try: python ai_navigator.py"

