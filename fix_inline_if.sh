#!/usr/bin/env bash
set -euo pipefail

FILE="ai_navigator.py"
BACKUP="ai_navigator.py.bak.inlineif-$(date +%Y%m%d-%H%M%S)"

[[ -f "$FILE" ]] || { echo "ERROR: $FILE not found"; exit 1; }
cp -v "$FILE" "$BACKUP"

python3 - <<'PY'
import re
from pathlib import Path

p = Path("ai_navigator.py")
s = p.read_text()

# 1) Specific common case: "row = cur.fetchone(); if not row: return"
def fix_fetchone_inline(m):
    indent = m.group(1)
    var = m.group(2)
    return f"{indent}{var} = cur.fetchone()\n{indent}if not {var}:\n{indent}    return"

s_new = re.sub(
    r"^(\s*)(\w+)\s*=\s*cur\.fetchone\(\);\s*if\s+not\s+\2\s*:\s*return\s*$",
    fix_fetchone_inline,
    s,
    flags=re.M,
)

# 2) Generic fallback: transform any "; if not X: return" into newline block,
#    preserving indent. (Conservative—only when it’s at line end.)
def fix_generic_inline(m):
    indent = m.group(1)
    before = m.group(2).rstrip()
    cond = m.group(3).strip()
    return f"{indent}{before}\n{indent}if not {cond}:\n{indent}    return"

s_new = re.sub(
    r"^(\s*)(.+?);\s*if\s+not\s+(.+?)\s*:\s*return\s*$",
    fix_generic_inline,
    s_new,
    flags=re.M,
)

if s_new != s:
    p.write_text(s_new)
    print("Inline `; if not ...: return` patterns fixed.")
else:
    print("No inline patterns found; file unchanged.")
PY

echo "Done. Now run: python ai_navigator.py"

