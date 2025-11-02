#!/usr/bin/env bash
set -euo pipefail

# push.sh (one-shot release script)
# --- Config you can override via env (sane defaults) -------------------------
: "${BRANCH:=main}"
: "${TAG:=v0.2}"
: "${TAG_MSG:=v0.2 – Stable Archive + Memory Weave + Honest VPN UI}"
: "${REMOTE:=origin}"
: "${REPO_URL:=}"   # leave empty to keep existing remote

# --- Sanity ------------------------------------------------------------------
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not in a git repo. Aborting." >&2
  exit 1
fi

# Optional: set/confirm remote URL
if [ -n "$REPO_URL" ]; then
  if git remote | grep -qx "$REMOTE"; then
    git remote set-url "$REMOTE" "$REPO_URL"
  else
    git remote add "$REMOTE" "$REPO_URL"
  fi
fi

echo "Remote $REMOTE → $(git remote get-url $REMOTE)"

# Ensure branch and default
git checkout "$BRANCH"
git branch --set-upstream-to="$REMOTE/$BRANCH" "$BRANCH" || true

# Stage Release.md and scripts/push.sh if they exist
git add -A

# Compose a crisp commit message if there are changes
if ! git diff --cached --quiet; then
  git commit -m "feat: stable reader-mode archive + Memory Weave + honest VPN indicator

- Archive: sanitize HTML, embed images (data URIs), dedupe by content hash
- Memory Weave: context capsules for Recover & Recover to ChatGPT
- VPN: systemd OpenVPN with bright ACTIVE/OFF/CONNECTING indicator
- DB: indices on captured_at/url; resources table for image BLOBs
- UX: throbber + concise status text"
fi

# Tag (idempotent: recreate if exists locally and matches)
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists locally. Skipping tag create."
else
  git tag -a "$TAG" -m "$TAG_MSG"
fi

# Push branch + tags
git push "$REMOTE" "$BRANCH"
git push "$REMOTE" "$TAG" || true

# Set remote HEAD to main (first time only)
git remote set-head "$REMOTE" -a || true

echo "✔ Pushed $BRANCH and $TAG to $REMOTE."
