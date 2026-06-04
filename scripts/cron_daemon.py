#!/usr/bin/env python3
"""
cron_daemon.py — Persistent Python daemon for OpenClaude cron.

Replaces the Bash daemon loop to eliminate Python cold-start on every tick.
Features:
  - Persistent Python interpreter (no fork-per-tick)
  - Catch-up after sleep/hibernate (runs missed ticks)
  - Heartbeat file for health monitoring
  - Signal handling for graceful shutdown
  - One bad task doesn't skip all others
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.expanduser("~/.openclaude/cron")
TASKS_FILE = os.path.join(DATA_DIR, "cron-tasks.json")
PID_FILE = os.path.join(DATA_DIR, "cron.pid")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat")
HEARTBEAT_STALE_SECONDS = 180  # 3x tick = stale
LOG_DIR = os.path.join(DATA_DIR, "logs")
RUNNING_DIR = os.path.join(DATA_DIR, "running")
JSON_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cron_json_helper.py")
OPENCLAUDE_BIN = os.environ.get("OPENCLAUDE_BIN", "openclaude")

TICK_INTERVAL = int(os.environ.get("CRON_TICK_INTERVAL", "60"))
TASK_TIMEOUT = int(os.environ.get("CRON_TIMEOUT", "300"))
MAX_CONCURRENT = int(os.environ.get("CRON_MAX_CONCURRENT", "3"))

# ── State ──────────────────────────────────────────────────────────────────────
running_tasks = {}  # task_id -> pid
shutdown_requested = False


# ── Signal Handling ────────────────────────────────────────────────────────────
def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True


# ── Task Execution ─────────────────────────────────────────────────────────────
def get_running_count():
    """Count running task marker files."""
    if not os.path.isdir(RUNNING_DIR):
        return 0
    return len([f for f in os.listdir(RUNNING_DIR) if f.endswith(".pid")])


def run_task(task_id, prompt):
    """Execute a task via openclaude -p in a subprocess."""
    if task_id in running_tasks:
        pid = running_tasks[task_id]
        try:
            os.kill(pid, 0)
            return  # already running
        except OSError:
            del running_tasks[task_id]

    if get_running_count() >= MAX_CONCURRENT:
        print(f"WARN: Concurrency limit ({MAX_CONCURRENT}) reached — skipping '{task_id}'", file=sys.stderr)
        return

    logfile = os.path.join(LOG_DIR, f"{task_id}.log")
    os.makedirs(LOG_DIR, exist_ok=True)

    # Launch task in subprocess (non-blocking)
    proc = subprocess.Popen(
        [OPENCLAUDE_BIN, "-p", prompt,
         "--dangerously-skip-permissions",
         "--output-format", "text"],
        stdout=open(logfile, "a"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,  # new process group for clean kill
    )
    running_tasks[task_id] = proc.pid
    print(f"INFO: Running task '{task_id}' (PID {proc.pid})", file=sys.stderr)


def reap_finished():
    """Check for finished tasks and clean up."""
    finished = []
    for task_id, pid in running_tasks.items():
        try:
            os.kill(pid, 0)
        except OSError:
            finished.append(task_id)

    for task_id in finished:
        del running_tasks[task_id]
        # Update last_run
        try:
            subprocess.run(
                [sys.executable, JSON_HELPER, "update-last-run", task_id],
                capture_output=True, timeout=5
            )
        except Exception:
            pass


# ── Heartbeat ──────────────────────────────────────────────────────────────────
def write_heartbeat():
    """Write current timestamp to heartbeat file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(datetime.now().isoformat())


def is_heartbeat_stale():
    """Check if heartbeat is stale (>3x tick interval)."""
    if not os.path.exists(HEARTBEAT_FILE):
        return True
    try:
        with open(HEARTBEAT_FILE) as f:
            last = datetime.fromisoformat(f.read().strip())
        age = (datetime.now() - last).total_seconds()
        return age > HEARTBEAT_STALE_SECONDS
    except (ValueError, OSError):
        return True


# ── Main Loop ──────────────────────────────────────────────────────────────────
def daemon_loop():
    global shutdown_requested

    # Write PID
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    last_tick_time = time.time()

    print(f"INFO: Daemon started (PID {os.getpid()}, tick={TICK_INTERVAL}s, timeout={TASK_TIMEOUT}s, max_concurrent={MAX_CONCURRENT})", file=sys.stderr)

    while not shutdown_requested:
        now = time.time()
        elapsed = now - last_tick_time

        # Catch-up: if we missed ticks (e.g. after sleep), run them
        missed_ticks = 0
        if elapsed > TICK_INTERVAL * 2:
            missed_ticks = int(elapsed / TICK_INTERVAL) - 1
            if missed_ticks > 0:
                print(f"INFO: Catching up — {missed_ticks} missed tick(s) after {int(elapsed)}s gap", file=sys.stderr)

        # Reap finished tasks
        reap_finished()

        # Check for due tasks (run catch-up ticks too)
        ticks_to_run = 1 + missed_ticks
        for i in range(ticks_to_run):
            # Get due tasks from Python helper
            now_min = datetime.now().strftime("%-M")
            now_hour = datetime.now().strftime("%-H")
            now_dom = datetime.now().strftime("%-d")
            now_month = datetime.now().strftime("%-m")
            now_dow = datetime.now().strftime("%-w")

            try:
                result = subprocess.run(
                    [sys.executable, JSON_HELPER, "due-tasks",
                     now_min, now_hour, now_dom, now_month, now_dow],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split("\t", 2)
                    if len(parts) == 3:
                        task_id, schedule, prompt = parts
                        run_task(task_id, prompt)
            except subprocess.TimeoutExpired:
                print("WARN: JSON helper timed out", file=sys.stderr)
            except Exception as e:
                print(f"ERROR: Failed to check tasks: {e}", file=sys.stderr)

            # Write heartbeat after each tick
            write_heartbeat()

        last_tick_time = time.time()

        # Sleep in small increments so we respond to signals quickly
        sleep_end = time.time() + TICK_INTERVAL
        while time.time() < sleep_end and not shutdown_requested:
            time.sleep(min(1, sleep_end - time.time()))

    # Shutdown: kill running tasks
    print("INFO: Shutting down...", file=sys.stderr)
    for task_id, pid in running_tasks.items():
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
    # Clean up
    for f in [PID_FILE, HEARTBEAT_FILE]:
        try:
            os.unlink(f)
        except OSError:
            pass
    print("INFO: Daemon stopped", file=sys.stderr)


# ── Entry Points ───────────────────────────────────────────────────────────────
def cmd_start():
    """Start daemon in background."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"ERROR: Daemon already running (PID {pid})")
            sys.exit(1)
        except (OSError, ValueError):
            os.unlink(PID_FILE)

    # Fork to background
    pid = os.fork()
    if pid > 0:
        print(f"Daemon started (PID {pid})")
        return

    # Child: create new session, redirect stdio
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)

    daemon_loop()


def cmd_stop():
    """Stop daemon by sending SIGTERM."""
    if not os.path.exists(PID_FILE):
        print("No daemon running")
        return

    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Stopping daemon (PID {pid})...")
        # Wait up to 10s
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(1)
            except OSError:
                print("Daemon stopped")
                return
        # Force kill
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        print("Daemon force killed")
    except (ValueError, OSError) as e:
        print(f"Error: {e}")
    finally:
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass


def cmd_status():
    """Check daemon health."""
    if not os.path.exists(PID_FILE):
        print("Daemon: not running")
        return

    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        print(f"Daemon: not running (stale PID file)")
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass
        return

    # Check heartbeat
    if is_heartbeat_stale():
        print(f"Daemon: running but STALE (PID {pid}) — no heartbeat in {HEARTBEAT_STALE_SECONDS}s")
    else:
        print(f"Daemon: running (PID {pid})")

    # Task count
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE) as f:
                data = json.load(f)
            print(f"Tasks: {len(data.get('tasks', []))} configured")
        except (json.JSONDecodeError, OSError):
            print("Tasks: error reading tasks.json")
    else:
        print("Tasks: none")

    # Running tasks
    running = len(running_tasks)
    print(f"Running: {running} task(s)")

    # Log count
    if os.path.isdir(LOG_DIR):
        log_count = len([f for f in os.listdir(LOG_DIR) if f.endswith(".log")])
        print(f"Logs: {log_count}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: cron_daemon.py {start|stop|status}")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
