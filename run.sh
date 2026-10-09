#!/bin/bash
# asc-pull wrapper for Unraid (User Scripts plugin). Full guide: SETUP.md
#
#   run.sh            weekly job: pull from Apple, then upload to Google Drive
#   run.sh pull       pull only
#   run.sh upload     upload only (re-send out/latest to Drive; optional, see ASC_SKIP_UPLOAD)
#   run.sh check      test each Apple key with harmless read calls
#   run.sh setup      one-off: create the Apple analytics report requests (Admin key)
#   run.sh rclone     one-off: link Google Drive (interactive)
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${ASC_DOCKER:-docker}"
PY_IMAGE="${ASC_PY_IMAGE:-python:3.12-slim}"
RCLONE_IMAGE="${ASC_RCLONE_IMAGE:-rclone/rclone:latest}"
NOTIFY=/usr/local/emhttp/webGui/scripts/notify
CONF="$APP_DIR/config.env"
LOG="$APP_DIR/out/run.log"

mkdir -p "$APP_DIR/out" "$APP_DIR/keys" "$APP_DIR/rclone"
# Files copied over SMB arrive world-readable: lock down secrets and business data.
chmod 600 "$APP_DIR"/config.env "$APP_DIR"/keys/* 2>/dev/null
chmod 700 "$APP_DIR/keys" "$APP_DIR/out" "$APP_DIR/rclone" 2>/dev/null

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

notify_alert() {
  if [ -x "$NOTIFY" ]; then "$NOTIFY" -e "asc-pull" -s "$1" -d "$2" -i alert; fi
  return 0
}

# Reads KEY=value from config.env (used for bash-side settings only).
conf() {
  local v
  v="$(grep -E "^$1=" "$CONF" 2>/dev/null | tail -1 | cut -d= -f2-)"
  v="${v%\"}"; v="${v#\"}"
  echo "$v"
}

dest() {
  local d
  d="$(grep -E '^RCLONE_DEST=' "$CONF" 2>/dev/null | tail -1 | cut -d= -f2-)"
  d="${d%\"}"; d="${d#\"}"
  echo "${d:-gdrive:App Store Analytics}"
}

# Runs asc_pull.py inside a throwaway Python container. Dependencies are installed
# once into ./.deps and reused.
py() {
  "$DOCKER" run --rm -v "$APP_DIR":/app -w /app "$PY_IMAGE" sh -c '
    if [ ! -f /app/.deps/.ok ]; then
      rm -rf /app/.deps
      pip install --quiet --no-cache-dir --target /app/.deps -r /app/requirements.txt \
        && touch /app/.deps/.ok || exit 3
    fi
    PYTHONPATH=/app/.deps exec python /app/asc_pull.py --env-file /app/config.env "$@"
  ' sh "$@"
}

# Google Drive cannot read plain .md or .csv, so files are uploaded as .txt and .csv
# and converted by rclone into Google Docs and a Google Sheet.
upload() {
  if [ ! -f "$APP_DIR/rclone/rclone.conf" ]; then log "rclone is not set up yet: run ./run.sh rclone"; return 2; fi
  if [ ! -f "$APP_DIR/out/latest/STATUS.md" ]; then log "nothing to upload yet: run ./run.sh pull first"; return 2; fi
  local stage="$APP_DIR/out/stage" d f
  d="$(dest)"
  rm -rf "$stage"; mkdir -p "$stage"
  for f in "$APP_DIR"/out/latest/*.md; do
    cp "$f" "$stage/$(basename "${f%.md}").txt"
  done
  if [ -f "$APP_DIR/out/latest/history_weekly.csv" ]; then cp "$APP_DIR/out/latest/history_weekly.csv" "$stage/"; fi
  local rc=("$DOCKER" run --rm -v "$APP_DIR/rclone":/config/rclone -v "$stage":/data:ro "$RCLONE_IMAGE")
  # Fresh upload each week: remove last week's folder (goes to Drive bin), then copy.
  "${rc[@]}" purge "$d/latest" >>"$LOG" 2>&1 || true
  "${rc[@]}" copy /data "$d/latest" --drive-import-formats txt,csv -v >>"$LOG" 2>&1
}

rclone_config() {
  "$DOCKER" run --rm -it -v "$APP_DIR/rclone":/config/rclone "$RCLONE_IMAGE" config
  chmod 600 "$APP_DIR/rclone/rclone.conf" 2>/dev/null
  return 0
}

weekly() {
  local pc uc
  log "weekly pull starting"
  py 2>&1 | tee -a "$LOG"
  pc=${PIPESTATUS[0]}
  if [ "$pc" -ne 0 ]; then
    log "pull FAILED (exit $pc)"
    notify_alert "App Store pull failed" "Open $APP_DIR/out/latest/STATUS.md"
  fi
  # Upload even after a failed pull, so the FAILED status reaches the weekly email.
  if [ "$(conf ASC_SKIP_UPLOAD)" = "1" ]; then
    log "Drive upload skipped (ASC_SKIP_UPLOAD=1); files stay in out/latest"
    uc=0
  else
    upload; uc=$?
  fi
  if [ "$uc" -ne 0 ]; then
    log "upload failed or skipped (exit $uc)"
    notify_alert "App Store upload problem" "See $LOG"
  else
    log "upload done"
  fi
  tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
  if [ "$pc" -ne 0 ]; then return "$pc"; fi
  return "$uc"
}

case "${1:-weekly}" in
  weekly) weekly ;;
  pull)   py 2>&1 | tee -a "$LOG"; exit "${PIPESTATUS[0]}" ;;
  upload) upload ;;
  check)  py --check ;;
  setup)  py --setup ;;
  rclone) rclone_config ;;
  *) echo "usage: run.sh [weekly|pull|upload|check|setup|rclone]"; exit 64 ;;
esac
