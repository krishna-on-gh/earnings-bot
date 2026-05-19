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
git config user.email "bot@bot.com"
git config user.name "EarningsBot"

if [ -z "$GH_PAT" ] || [ -z "$GH_REPO" ]; then
    echo "ERROR: GH_PAT and GH_REPO must be set as Railway environment variables."
    exit 1
fi

# Embed PAT in remote URL so git can push without interactive auth.
# Use credential store so the token doesn't appear in git error output.
git config credential.helper store
echo "https://oauth2:${GH_PAT}@github.com" > ~/.git-credentials
chmod 600 ~/.git-credentials
git remote set-url origin "https://github.com/${GH_REPO}.git"

# Pull latest state (other services may have committed data changes since deploy)
git pull --rebase origin main

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
