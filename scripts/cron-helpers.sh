#!/usr/bin/env bash
# cron-helpers.sh — Shared utilities for the OpenClaude cron skill
# Provides: atomic JSON writes, file locking, logging, slugification, log rotation
set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────────────────
CRON_DATA_DIR="${CRON_DATA_DIR:-$HOME/.openclaude/cron}"
CRON_TASKS_FILE="$CRON_DATA_DIR/cron-tasks.json"
CRON_LOCK_FILE="$CRON_DATA_DIR/cron.lock"
CRON_PID_FILE="$CRON_DATA_DIR/cron.pid"
CRON_LOG_DIR="$CRON_DATA_DIR/logs"
CRON_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_JSON_HELPER="$CRON_SCRIPT_DIR/cron_json_helper.py"
CRON_MAX_LOG_LINES="${CRON_MAX_LOG_LINES:-500}"

# ── Initialization ─────────────────────────────────────────────────────────────
cron_init() {
    mkdir -p "$CRON_DATA_DIR" "$CRON_LOG_DIR"
    if [ ! -f "$CRON_TASKS_FILE" ]; then
        echo '{"tasks":[]}' > "$CRON_TASKS_FILE"
    fi
}

# ── Logging ────────────────────────────────────────────────────────────────────
_log() {
    local level="$1" msg="$2"
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg"
}

log_info()  { _log "INFO"  "$*" >&2; }
log_warn()  { _log "WARN"  "$*" >&2; }
log_error() { _log "ERROR" "$*" >&2; }

log_task() {
    local task_id="$1" msg="$2"
    # Validate task_id to prevent path traversal
    if ! valid_task_id "$task_id"; then
        log_error "Invalid task_id for logging: '$task_id'"
        return 1
    fi
    local logfile="$CRON_LOG_DIR/${task_id}.log"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$msg" >> "$logfile"
    _rotate_log "$logfile"
}

# ── Log Rotation ───────────────────────────────────────────────────────────────
_rotate_log() {
    local logfile="$1"
    [ -f "$logfile" ] || return 0
    local lines
    lines=$(wc -l < "$logfile" 2>/dev/null || echo 0)
    if [ "$lines" -gt "$CRON_MAX_LOG_LINES" ]; then
        local tmp
        tmp=$(mktemp "${logfile}.XXXXXX")
        tail -n "$CRON_MAX_LOG_LINES" "$logfile" > "$tmp"
        mv "$tmp" "$logfile"
    fi
}

# ── JSON Helpers ───────────────────────────────────────────────────────────────
# Read a JSON file safely with fallback
json_read() {
    local file="$1"
    if [ -f "$file" ] && [ -s "$file" ]; then
        cat "$file"
    else
        echo '{"tasks":[]}'
    fi
}

# Atomic write: write to temp file then mv (atomic on POSIX)
json_write() {
    local file="$1" content="$2"
    local tmp
    tmp=$(mktemp "${file}.XXXXXX")
    echo "$content" > "$tmp"
    mv -f "$tmp" "$file"
}

# ── Task Count (delegates to Python for accuracy) ──────────────────────────────
task_count() {
    if [ -f "$CRON_JSON_HELPER" ]; then
        python3 "$CRON_JSON_HELPER" task-count
    else
        echo "0"
    fi
}

# ── Locking (file-based, no eval) ──────────────────────────────────────────────
# Open exclusive lock on fd 9. Returns immediately if lock is held.
cron_lock() {
    mkdir -p "$(dirname "$CRON_LOCK_FILE")"
    exec 9>"$CRON_LOCK_FILE"
    flock -x 9
}

# Release lock on fd 9
cron_unlock() {
    exec 9>&-
}

# ── Task ID Slugification ─────────────────────────────────────────────────────
slugify() {
    echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

# ── Validate Task ID ───────────────────────────────────────────────────────────
# Only allow alphanumeric + hyphens + underscores, 1-64 chars
valid_task_id() {
    local id="$1"
    echo "$id" | grep -qE '^[a-zA-Z0-9_-]{1,64}$'
}

# ── PID Management ─────────────────────────────────────────────────────────────
pid_is_running() {
    local pid="$1"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

pid_write() {
    echo "$$" > "$CRON_PID_FILE"
}

pid_read() {
    if [ -f "$CRON_PID_FILE" ]; then
        cat "$CRON_PID_FILE"
    else
        echo ""
    fi
}

pid_cleanup() {
    rm -f "$CRON_PID_FILE"
}

# Check if another daemon is already running; if so, report and exit
ensure_single_daemon() {
    local existing_pid
    existing_pid=$(pid_read)
    if [ -n "$existing_pid" ] && pid_is_running "$existing_pid"; then
        log_error "Daemon already running (PID $existing_pid)"
        log_error "Use 'cron stop' first, or remove $CRON_PID_FILE if stale"
        exit 1
    fi
    # Stale PID file
    pid_cleanup
}
