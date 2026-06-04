#!/usr/bin/env bash
# cron-daemon.sh — Background scheduler for OpenClade cron tasks
# Runs tasks by executing `openclaude -p "<prompt>"` on configured schedules.
#
# Usage:
#   cron-daemon.sh start    — Start the daemon in background
#   cron-daemon.sh stop     — Stop the running daemon
#   cron-daemon.sh status   — Check if daemon is running
#
# Environment variables:
#   CRON_TICK_INTERVAL  — Seconds between checks (default: 60)
#   CRON_TIMEOUT        — Max seconds per task execution (default: 300)
#   CRON_MAX_CONCURRENT — Max tasks running in parallel (default: 3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/cron-helpers.sh"
. "$SCRIPT_DIR/cron-parse.sh"

CRON_TICK="${CRON_TICK_INTERVAL:-60}"
CRON_TIMEOUT="${CRON_TIMEOUT:-300}"
CRON_MAX_PAR="${CRON_MAX_CONCURRENT:-3}"
CRON_RUNNING_DIR="$CRON_DATA_DIR/running"
OPENCLAUDE_BIN="${OPENCLAUDE_BIN:-openclaude}"

# ── Trap Handlers ──────────────────────────────────────────────────────────────
_cleanup() {
    log_info "Daemon shutting down (PID $$)"
    pid_cleanup
    # Kill any remaining child processes
    kill 0 2>/dev/null || true
    wait 2>/dev/null || true
}

_handle_sighup() {
    log_info "Received SIGHUP — reloading task config"
    # Config reload happens on next tick automatically
}

_handle_sigterm() { _cleanup; exit 0; }
_handle_sigint()  { _cleanup; exit 0; }

# ── Reap Zombies ───────────────────────────────────────────────────────────────
_reaper() {
    while true; do
        wait -n 2>/dev/null || break
    done
}

# ── Run a Single Task ─────────────────────────────────────────────────────────
_run_task() {
    local task_id="$1" prompt="$2"
    local logfile="$CRON_LOG_DIR/${task_id}.log"
    local marker="$CRON_RUNNING_DIR/${task_id}.pid"

    # Concurrency check: skip if already running
    if [ -f "$marker" ]; then
        local running_pid
        running_pid=$(cat "$marker" 2>/dev/null || echo "")
        if [ -n "$running_pid" ] && pid_is_running "$running_pid"; then
            log_warn "Task '$task_id' already running (PID $running_pid) — skipping"
            return 0
        fi
        # Stale marker
        rm -f "$marker"
    fi

    # Concurrency limit check
    local running_count
    running_count=$(ls "$CRON_RUNNING_DIR"/*.pid 2>/dev/null | wc -l || echo 0)
    if [ "$running_count" -ge "$CRON_MAX_PAR" ]; then
        log_warn "Concurrency limit ($CRON_MAX_PAR) reached — skipping task '$task_id'"
        return 0
    fi

    log_info "Running task '$task_id'"
    log_task "$task_id" "--- Run started ---"

    # Write PID marker
    mkdir -p "$CRON_RUNNING_DIR"
    echo "$$" > "$marker"

    # Execute the task with timeout
    local exit_code=0
    timeout "$CRON_TIMEOUT" \
        "$OPENCLAUDE_BIN" -p "$prompt" \
        --dangerously-skip-permissions \
        --output-format text \
        2>>"$logfile" \
        >> "$logfile" || exit_code=$?

    # Clean up marker
    rm -f "$marker"

    if [ "$exit_code" -eq 0 ]; then
        log_task "$task_id" "--- Run succeeded ---"
        log_info "Task '$task_id' completed successfully"
    elif [ "$exit_code" -eq 124 ]; then
        log_task "$task_id" "--- Run timed out (${CRON_TIMEOUT}s) ---"
        log_warn "Task '$task_id' timed out after ${CRON_TIMEOUT}s"
    else
        log_task "$task_id" "--- Run failed (exit code: $exit_code) ---"
        log_warn "Task '$task_id' failed with exit code $exit_code"
    fi
}

# ── Check and Run Due Tasks ────────────────────────────────────────────────────
_check_tasks() {
    local content now_min now_hour now_dom now_month now_dow
    content=$(json_read "$CRON_TASKS_FILE")

    now_min=$(date '+%-M')
    now_hour=$(date '+%-H')
    now_dom=$(date '+%-d')
    now_month=$(date '+%-m')
    now_dow=$(date '+%-w')

    # Parse tasks — extract id, schedule, enabled for each task
    # Using awk to parse the JSON structure
    echo "$content" | awk -v min="$now_min" -v hour="$now_hour" -v dom="$now_dom" -v month="$now_month" -v dow="$now_dow" '
    BEGIN { in_tasks = 0; task_count = 0; }
    /"tasks"/ { in_tasks = 1; next }
    in_tasks && /"id"/ {
        gsub(/.*"id"[[:space:]]*:[[:space:]]*"/, ""); gsub(/".*/, "");
        current_id = $0;
        next
    }
    in_tasks && /"schedule"/ {
        gsub(/.*"schedule"[[:space:]]*:[[:space:]]*"/, ""); gsub(/".*/, "");
        current_schedule = $0;
        next
    }
    in_tasks && /"prompt"/ {
        gsub(/.*"prompt"[[:space:]]*:[[:space:]]*"/, ""); gsub(/".*/, "");
        current_prompt = $0;
        next
    }
    in_tasks && /"enabled"/ {
        gsub(/.*"enabled"[[:space:]]*:[[:space:]]*/, ""); gsub(/,.*/, "");
        gsub(/[[:space:]]/, "");
        current_enabled = $0;
        if (current_id != "" && current_enabled == "true") {
            print current_id "|" current_schedule "|" current_prompt;
        }
        current_id = ""; current_schedule = ""; current_prompt = "";
        next
    }
    '
}

# ── Main Loop ──────────────────────────────────────────────────────────────────
_daemon_loop() {
    log_info "Daemon started (PID $$, tick=${CRON_TICK}s, timeout=${CRON_TIMEOUT}s, max_concurrent=${CRON_MAX_PAR})"
    pid_write

    while true; do
        # Check for due tasks
        local due_line
        while IFS= read -r due_line; do
            [ -z "$due_line" ] && continue
            local task_id schedule prompt
            task_id=$(echo "$due_line" | cut -d'|' -f1)
            schedule=$(echo "$due_line" | cut -d'|' -f2)
            prompt=$(echo "$due_line" | cut -d'|' -f3-)

            # Check cron match
            if cron_matches "$schedule" \
                "$(date '+%-M')" \
                "$(date '+%-H')" \
                "$(date '+%-d')" \
                "$(date '+%-m')" \
                "$(date '+%-w')"; then
                # Run task in background subshell
                ( _run_task "$task_id" "$prompt" ) &
            fi
        done < <(_check_tasks)

        # Reap any finished children
        _reaper 2>/dev/null || true

        sleep "$CRON_TICK"
    done
}

# ── Command Dispatch ───────────────────────────────────────────────────────────
cmd_start() {
    cron_init
    ensure_single_daemon

    log_info "Starting daemon..."
    # Daemonize: fork, setsid, fork again (classic double-fork)
    (
        # First fork
        if [ "$(id -u)" -ne 0 ]; then
            setsid bash "$0" _foreground &
        else
            bash "$0" _foreground &
        fi
        disown
    )
    sleep 1
    if [ -f "$CRON_PID_FILE" ]; then
        log_info "Daemon started (PID $(pid_read))"
    else
        log_error "Failed to start daemon"
        exit 1
    fi
}

cmd_stop() {
    local pid
    pid=$(pid_read)
    if [ -z "$pid" ]; then
        log_warn "No daemon PID found"
        return 0
    fi
    if pid_is_running "$pid" ]; then
        log_info "Stopping daemon (PID $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        # Wait up to 5s for graceful shutdown
        local waited=0
        while pid_is_running "$pid" && [ "$waited" -lt 5 ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if pid_is_running "$pid" ]; then
            log_warn "Force killing daemon (PID $pid)"
            kill -KILL "$pid" 2>/dev/null || true
        fi
        log_info "Daemon stopped"
    else
        log_warn "Daemon PID $pid is not running (stale)"
    fi
    pid_cleanup
    # Clean up running markers
    rm -rf "$CRON_RUNNING_DIR"
}

cmd_status() {
    local pid
    pid=$(pid_read)
    if [ -n "$pid" ] && pid_is_running "$pid" ]; then
        echo "Daemon: running (PID $pid)"
    else
        echo "Daemon: not running"
        [ -n "$pid" ] && echo "  (stale PID file: $pid)"
    fi

    if [ -f "$CRON_TASKS_FILE" ]; then
        local count
        count=$(task_count)
        echo "Tasks: $count configured"
    else
        echo "Tasks: none (no tasks.json)"
    fi

    if [ -d "$CRON_LOG_DIR" ]; then
        local log_count
        log_count=$(ls "$CRON_LOG_DIR"/*.log 2>/dev/null | wc -l || echo 0)
        echo "Logs: $log_count task log(s)"
    fi
}

# ── Entry Point ────────────────────────────────────────────────────────────────
case "${1:-help}" in
    start)      cmd_start ;;
    stop)       cmd_stop ;;
    status)     cmd_status ;;
    _foreground)
        # Internal: run the daemon loop in foreground
        trap '_handle_sigterm' TERM
        trap '_handle_sigint' INT
        trap '_handle_sighup' HUP
        _daemon_loop
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
