#!/usr/bin/env python3
"""
cron_daemon.py — Persistent Python daemon for OpenClaude cron.

Replaces the Bash daemon loop to eliminate Python cold-start on every tick.
Features:
  - Persistent Python interpreter (no fork-per-tick)
  - Catch-up after sleep/hibernate (runs missed ticks at correct times)
  - Heartbeat file for health monitoring
  - Signal handling for graceful shutdown
  - One bad task doesn't skip all others
  - File locking prevents data races
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.expanduser("~/.openclaude/cron")
TASKS_FILE = os.path.join(DATA_DIR, "cron-tasks.json")
TASKS_LOCK = TASKS_FILE + ".lock"
PID_FILE = os.path.join(DATA_DIR, "cron.pid")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat")
HEARTBEAT_STALE_SECONDS = 180  # 3x tick = stale
LOG_DIR = os.path.join(DATA_DIR, "logs")
MAX_LOG_LINES = 500
MAX_LOG_DIR_SIZE = 10 * 1024 * 1024  # 10MB total log directory cap
JSON_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cron_json_helper.py")
OPENCLAUDE_BIN = os.environ.get("OPENCLAUDE_BIN", "openclaude")

# Validate OPENCLAUDE_BIN points to a real binary
def _validate_bin():
    """Check OPENCLAUDE_BIN exists and is not a symlink to somewhere unexpected."""
    import shutil
    resolved = shutil.which(OPENCLAUDE_BIN)
    if not resolved:
        print(f"ERROR: '{OPENCLAUDE_BIN}' not found in PATH", file=sys.stderr)
        sys.exit(1)
    # Warn if it's a symlink (not blocking, but audible)
    if os.path.islink(OPENCLAUDE_BIN):
        real = os.path.realpath(OPENCLAUDE_BIN)
        print(f"WARN: OPENCLAUDE_BIN={OPENCLAUDE_BIN} is a symlink -> {real}", file=sys.stderr)

_validate_bin()

TICK_INTERVAL = int(os.environ.get("CRON_TICK_INTERVAL", "60"))
TASK_TIMEOUT = int(os.environ.get("CRON_TIMEOUT", "300"))
MAX_CONCURRENT = int(os.environ.get("CRON_MAX_CONCURRENT", "3"))
MAX_CATCHUP_TICKS = 10

# ── State ──────────────────────────────────────────────────────────────────────
running_tasks = {}  # task_id -> proc
task_exit_codes = {}  # task_id -> exit code (populated on reap)
shutdown_requested = False


# ── Signal Handling ────────────────────────────────────────────────────────────
def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True


# ── File Locking ───────────────────────────────────────────────────────────────
def load_tasks_locked():
    """Load tasks with file lock held."""
    if not os.path.exists(TASKS_FILE):
        return {"tasks": []}
    try:
        with open(TASKS_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return data
    except (json.JSONDecodeError, OSError):
        return {"tasks": []}


def save_tasks_locked(data):
    """Save tasks with exclusive file lock."""
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(TASKS_FILE), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        os.replace(tmp_path, TASKS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Task Execution ─────────────────────────────────────────────────────────────
def run_task(task_id, prompt):
    """Execute a task via openclaude -p in a subprocess."""
    # Validate task_id (defense-in-depth: daemon re-validates what JSON says)
    if not task_id or not task_id.strip():
        return
    import re
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', task_id):
        print(f"WARN: Skipping task with invalid ID: '{task_id}'", file=sys.stderr)
        return

    if task_id in running_tasks:
        proc = running_tasks[task_id]
        try:
            os.kill(proc.pid, 0)
            return  # already running
        except OSError:
            del running_tasks[task_id]

    # Use in-memory count (not file-based — files are never populated)
    if len(running_tasks) >= MAX_CONCURRENT:
        print(f"WARN: Concurrency limit ({MAX_CONCURRENT}) reached — skipping '{task_id}'", file=sys.stderr)
        return

    logfile = os.path.join(LOG_DIR, f"{task_id}.log")
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)

    # Rotate log if too large (check before open)
    try:
        if os.path.exists(logfile) and os.path.getsize(logfile) > 0:
            with open(logfile) as lf:
                line_count = sum(1 for _ in lf)
            if line_count > MAX_LOG_LINES:
                tmp = logfile + ".rot"
                with open(logfile) as lf:
                    lines = lf.readlines()
                with open(tmp, "w") as lf:
                    lf.writelines(lines[-MAX_LOG_LINES:])
                os.replace(tmp, logfile)
    except OSError:
        pass

    # Enforce total log directory size cap
    try:
        log_files = [(f, os.path.getsize(os.path.join(LOG_DIR, f)))
                     for f in os.listdir(LOG_DIR) if f.endswith(".log")]
        total = sum(s for _, s in log_files)
        if total > MAX_LOG_DIR_SIZE:
            # Remove oldest files first (by mtime) until under cap
            log_files.sort(key=lambda x: os.path.getmtime(os.path.join(LOG_DIR, x[0])))
            for fname, fsize in log_files:
                if total <= MAX_LOG_DIR_SIZE * 0.8:
                    break
                try:
                    os.unlink(os.path.join(LOG_DIR, fname))
                    total -= fsize
                except OSError:
                    pass
    except OSError:
        pass

    # Launch task in subprocess (non-blocking)
    # No --dangerously-skip-permissions: relies on settings.json Bash(*) config
    log_fh = open(logfile, "a", buffering=1)  # line-buffered
    try:
        os.chmod(logfile, 0o600)
    except OSError:
        pass
    proc = subprocess.Popen(
        [OPENCLAUDE_BIN, "-p", prompt,
         "--output-format", "text"],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,  # new process group for clean kill
    )
    log_fh.close()  # fd passed to child, parent can close
    running_tasks[task_id] = proc
    print(f"INFO: Running task '{task_id}' (PID {proc.pid})", file=sys.stderr)


def reap_finished():
    """Check for finished tasks, reap zombies, record exit codes."""
    finished = []
    for task_id, proc in running_tasks.items():
        retcode = proc.poll()
        if retcode is not None:
            finished.append((task_id, retcode))

    for task_id, retcode in finished:
        del running_tasks[task_id]
        task_exit_codes[task_id] = retcode
        if retcode != 0:
            print(f"WARN: Task '{task_id}' failed with exit code {retcode}", file=sys.stderr)
        # Update last_run with file locking
        try:
            data = load_tasks_locked()
            for t in data.get("tasks", []):
                if t["id"] == task_id:
                    t["last_run"] = datetime.now().isoformat()
                    break
            save_tasks_locked(data)
        except Exception:
            pass


# ── Heartbeat ──────────────────────────────────────────────────────────────────
def write_heartbeat():
    """Write current timestamp to heartbeat file (atomic, restrictive perms)."""
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=DATA_DIR, suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(datetime.now().isoformat())
        os.replace(tmp_path, HEARTBEAT_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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

    # Write PID atomically with restrictive permissions
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=DATA_DIR, suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        os.replace(tmp_path, PID_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    last_tick_time = time.time()

    print(f"INFO: Daemon started (PID {os.getpid()}, tick={TICK_INTERVAL}s, timeout={TASK_TIMEOUT}s, max_concurrent={MAX_CONCURRENT})", file=sys.stderr)

    while not shutdown_requested:
        now = time.time()
        elapsed = now - last_tick_time

        # Calculate missed ticks for catch-up (capped to prevent runaway)
        missed_ticks = 0
        if elapsed > TICK_INTERVAL * 2:
            missed_ticks = int(elapsed / TICK_INTERVAL) - 1
            if missed_ticks > MAX_CATCHUP_TICKS:
                print(f"WARN: Capping catch-up from {missed_ticks} to {MAX_CATCHUP_TICKS} ticks", file=sys.stderr)
                missed_ticks = MAX_CATCHUP_TICKS
            if missed_ticks > 0:
                print(f"INFO: Catching up — {missed_ticks} missed tick(s) after {int(elapsed)}s gap", file=sys.stderr)

        # Reap finished tasks
        reap_finished()

        # Run catch-up ticks + current tick, each with CORRECT timestamps
        ticks_to_run = 1 + missed_ticks
        # Calculate the start time: go back missed_ticks * TICK_INTERVAL from now
        tick_start = datetime.now() - timedelta(seconds=ticks_to_run * TICK_INTERVAL)

        for i in range(ticks_to_run):
            tick_time = tick_start + timedelta(seconds=(i + 1) * TICK_INTERVAL)
            now_min = tick_time.strftime("%-M")
            now_hour = tick_time.strftime("%-H")
            now_dom = tick_time.strftime("%-d")
            now_month = tick_time.strftime("%-m")
            now_dow = tick_time.strftime("%-w")

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
                        # Decode escaped newlines/tabs from JSON helper
                        # Order matters: decode backslashes first to avoid double-decode
                        prompt = prompt.replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
                        run_task(task_id, prompt)
            except subprocess.TimeoutExpired:
                print("WARN: JSON helper timed out", file=sys.stderr)
            except Exception as e:
                print(f"ERROR: Failed to check tasks: {e}", file=sys.stderr)

        # Write heartbeat after tick(s)
        write_heartbeat()

        last_tick_time = time.time()

        # Sleep in small increments so we respond to signals quickly
        sleep_end = time.time() + TICK_INTERVAL
        while time.time() < sleep_end and not shutdown_requested:
            time.sleep(min(1, sleep_end - time.time()))

    # Shutdown: kill running tasks
    print("INFO: Shutting down...", file=sys.stderr)
    for task_id, proc in running_tasks.items():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
    # Clean up PID file
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass
    print("INFO: Daemon stopped", file=sys.stderr)


# ── Termux Wake Lock ─────────────────────────────────────────────────────────
def _termux_wake_lock(acquire):
    """Acquire or release a termux-wake-lock. No-op if not on Termux."""
    import shutil
    if not shutil.which("termux-wake-lock"):
        return
    try:
        if acquire:
            subprocess.Popen(["termux-wake-lock"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["termux-wake-unlock"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


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

    # Acquire termux wake lock if available (keeps CPU alive on Android)
    _termux_wake_lock(True)

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
    # Redirect stderr to daemon log file (not /dev/null)
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    daemon_log = os.path.join(LOG_DIR, "daemon.log")
    log_fd = os.open(daemon_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    daemon_loop()


def cmd_stop():
    """Stop daemon by sending SIGTERM."""
    if not os.path.exists(PID_FILE):
        print("No daemon running")
        _termux_wake_lock(False)
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
                _termux_wake_lock(False)
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
        # Only remove PID file if daemon is actually dead
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # still alive
            except (OSError, ValueError):
                try:
                    os.unlink(PID_FILE)
                except OSError:
                    pass
        _termux_wake_lock(False)


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
            data = load_tasks_locked()
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
