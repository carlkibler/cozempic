#!/bin/bash
# Daily backup of stats.db to private analytics repo
set -euo pipefail

ANALYTICS_DIR="${HOME}/.cozempic-analytics"
MEMORY_DIR="${HOME}/.claude/projects/-Users-ruya-Documents-Advisor-Cozempic/memory"

# Clone or pull
if [ -d "$ANALYTICS_DIR" ]; then
  cd "$ANALYTICS_DIR" && git pull --quiet 2>/dev/null || true
else
  git clone https://github.com/junaidtitan/cozempic-analytics.git "$ANALYTICS_DIR" 2>/dev/null || exit 0
fi

cd "$ANALYTICS_DIR"

# Copy latest files
cp "$MEMORY_DIR/stats.db" . 2>/dev/null || true
cp "$MEMORY_DIR/cozempic_stats.py" . 2>/dev/null || true
cp "$MEMORY_DIR/stats.json" . 2>/dev/null || true

# Commit and push if changed
if git diff --quiet && git diff --cached --quiet; then
  exit 0  # No changes
fi

git add -A
git commit -m "backup: $(date +%Y-%m-%d) stats.db" --quiet
git push --quiet 2>/dev/null || true
