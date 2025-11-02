#!/usr/bin/env bash
# ---------------------------------------------------------------
# AI Navigator Release Push Helper
# Usage: ./scripts/push.sh "v0.2.2 – Stable Memory, Honest Light"
# ---------------------------------------------------------------

set -e
tag_msg="${1:-v0.2.2 – Stable Memory, Honest Light}"

echo "==> Cleaning up sidecars and ensuring tree is clean..."
git rm --cached storage/*.db-shm storage/*.db-wal 2>/dev/null || true
echo -e "\n# SQLite sidecars\nstorage/*.db-shm\nstorage/*.db-wal\n" >> .gitignore
git add .gitignore
git add RELEASE.md
git commit -am "Release: ${tag_msg}" || echo "(No changes to commit)"

echo "==> Tagging release..."
git tag -a "$(echo $tag_msg | cut -d' ' -f1)" -m "${tag_msg}"

echo "==> Pushing to origin/main and tags..."
git push origin main
git push origin --tags

echo "==> Done. Release ${tag_msg} is live."

