#!/bin/bash
# Generic git-wrap runner for Railway cron services.
# Usage: bash scripts/run.sh <script_name> "<files to git add>"
# Example: bash scripts/run.sh scanner "data/recommendations.json data/scanner_status.json"
#
# Required Railway env vars:
#   GH_PAT   — GitHub Fine-grained PAT (Contents: read+write, this repo only)
#   GH_REPO  — repo in "username/reponame" format, e.g. "krish/earnings-bot"

set -e

SCRIPT="$1"
FILES="$2"

echo "========================================"
echo "  ${SCRIPT} starting at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================"

# ── Git setup ────────────────────────────────────────────────────────────────
if [ -z "$GH_PAT" ] || [ -z "$GH_REPO" ]; then
    echo "ERROR: GH_PAT and GH_REPO must be set as Railway environment variables."
    exit 1
fi

# Store credentials so git can push without interactive auth.
git config credential.helper store
echo "https://oauth2:${GH_PAT}@github.com" > ~/.git-credentials
chmod 600 ~/.git-credentials

# Railway's nixpacks build strips .git from the container.
# Detect this and reinitialize from the remote so git commands work.
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "No git directory found — initializing from remote..."
    git init -b main
    git remote add origin "https://github.com/${GH_REPO}.git"
    git fetch origin main --depth=1
    git reset --hard origin/main
    git branch --set-upstream-to=origin/main main
else
    git remote set-url origin "https://github.com/${GH_REPO}.git"
    git pull --rebase origin main
fi

git config user.email "bot@bot.com"
git config user.name "EarningsBot"

# ── Run script ───────────────────────────────────────────────────────────────
# Dashboard uses update_dashboard.py in root; everything else is in src/
if [ "$SCRIPT" = "dashboard" ]; then
    python update_dashboard.py
else
    python "src/${SCRIPT}.py"
fi

# ── Commit and push any data changes ────────────────────────────────────────
if [ -n "$FILES" ]; then
    # shellcheck disable=SC2086  # intentional word-splitting for FILES
    git add $FILES
    if git diff --cached --quiet; then
        echo "No data changes to commit."
    else
        git commit -m "${SCRIPT} run $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        git push origin main
        echo "Data changes pushed to GitHub."
    fi
fi

echo "========================================"
echo "  ${SCRIPT} complete"
echo "========================================"
