#!/usr/bin/env bash
set -euo pipefail

SCAN_DIR="${1:-$HOME/Documents}"
MAX_DEPTH=5
LOG_FILE="$HOME/Library/Logs/git-fetch-all.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec >>"$LOG_FILE" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting fetch sweep of $SCAN_DIR"

while IFS= read -r -d '' git_dir; do
  repo_dir="${git_dir%/.git}"
  if git -C "$repo_dir" fetch --all --prune --quiet; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ok      $repo_dir"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED  $repo_dir"
  fi
done < <(find "$SCAN_DIR" -maxdepth "$MAX_DEPTH" -type d -name ".git" -not -path "*/node_modules/*" -print0)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] done"
