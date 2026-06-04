#!/usr/bin/env python3
"""
cronctl.py — Task management CLI for OpenClaude cron.

Usage:
    cronctl.py list                        List all tasks
    cronctl.py add <id> <schedule> <prompt> Add a task
    cronctl.py remove <id>                 Remove a task
    cronctl.py enable <id>                 Enable a task
    cronctl.py disable <id>                Disable a task
    cronctl.py run <id>                    Run a task now (one-shot)
    cronctl.py log <id> [lines]            Show task logs
    cronctl.py test <cron-expr>            Test cron expression against current time
"""
import json
import os
import sys
import subprocess
import re
import tempfile
from datetime import datetime

TASKS_FILE = os.path.expanduser("~/.openclaude/cron/cron-tasks.json")
LOG_DIR = os.path.expanduser("~/.openclaude/cron/logs")

# ── Helpers ────────────────────────────────────────────────────────────────────

def ensure_file():
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    if not os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "w") as f:
            json.dump({"tasks": []}, f)


def load_tasks():
    ensure_file()
    try:
        with open(TASKS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: corrupted tasks.json: {e}", file=sys.stderr)
        # Backup corrupted file and start fresh
        backup = TASKS_FILE + ".corrupted"
        try:
            os.replace(TASKS_FILE, backup)
            print(f"Backed up corrupted file to {backup}", file=sys.stderr)
        except OSError:
            pass
        ensure_file()
        return {"tasks": []}


def save_tasks(data):
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(TASKS_FILE), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, TASKS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def valid_task_id(task_id):
    return bool(re.match(r'^[a-zA-Z0-9_-]{1,64}$', task_id))


def get_script_dir():
    return os.path.dirname(os.path.abspath(__file__))

# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_list():
    data = load_tasks()
    tasks = data.get("tasks", [])
    if not tasks:
        print("No scheduled tasks.")
        return

    # Dynamic column widths
    id_w = max(4, max(len(t["id"]) for t in tasks) + 2)
    sched_w = max(8, max(len(t.get("schedule", "")) for t in tasks) + 2)
    enabled_w = 10
    header = f"{'ID':<{id_w}} {'SCHEDULE':<{sched_w}} {'ENABLED':<{enabled_w}} {'LAST RUN'}"
    print(header)
    print("-" * len(header))
    for t in tasks:
        enabled = t.get("enabled", True)
        last_run = t.get("last_run") or "never"
        print(f"{t['id']:<{id_w}} {t.get('schedule',''):<{sched_w}} {str(enabled):<{enabled_w}} {last_run}")


MAX_PROMPT_LENGTH = 10240  # 10KB
MAX_TASKS = 100


def cmd_add(args):
    if len(args) < 3:
        print("Usage: cronctl.py add <id> <schedule> <prompt>")
        sys.exit(1)

    task_id, schedule, prompt = args[0], args[1], " ".join(args[2:])

    # Try human-readable schedule parsing
    parsed = parse_human_schedule(schedule)
    if parsed:
        print(f"  Interpreted '{schedule}' as '{parsed}'")
        schedule = parsed

    if not valid_task_id(task_id):
        print(f"Error: invalid task ID '{task_id}' (alphanumeric, hyphens, underscores, 1-64 chars)")
        sys.exit(1)

    fields = schedule.split()
    if len(fields) != 5:
        print(f"Error: cron expression must have 5 fields, got {len(fields)}")
        sys.exit(1)

    # Validate each cron field structurally
    for i, field in enumerate(fields):
        if not re.match(r'^[\d\*\/\-\,]+$', field):
            print(f"Error: invalid cron field '{field}' (field {i+1}) — only digits, *, /, -, , allowed")
            sys.exit(1)
        # Check for structural issues
        if field.startswith('-') or field.endswith('-'):
            print(f"Error: invalid cron field '{field}' (field {i+1}) — range must have both endpoints")
            sys.exit(1)
        if '//' in field:
            print(f"Error: invalid cron field '{field}' (field {i+1}) — double slash not allowed")
            sys.exit(1)
        if ',,' in field:
            print(f"Error: invalid cron field '{field}' (field {i+1}) — empty list segment")
            sys.exit(1)
        # Validate range endpoints and step are numeric
        for part in field.split('/'):
            for segment in part.split(','):
                if '-' in segment:
                    lo, hi = segment.split('-', 1)
                    if not lo.isdigit() or not hi.isdigit():
                        print(f"Error: invalid range '{segment}' in field {i+1} — endpoints must be numbers")
                        sys.exit(1)
        if '/' in field:
            step_part = field.rsplit('/', 1)[1]
            if not step_part.isdigit() or int(step_part) < 1:
                print(f"Error: invalid step '{step_part}' in field {i+1} — must be positive integer")
                sys.exit(1)
        # Check range size to prevent memory DoS
        base = field.split('/')[0] if '/' in field else field
        for segment in base.split(','):
            if '-' in segment:
                lo, hi = segment.split('-', 1)
                if lo.isdigit() and hi.isdigit() and int(hi) - int(lo) > 1000:
                    print(f"Error: range too large '{segment}' in field {i+1} (max 1000 values)")
                    sys.exit(1)

    if len(prompt) > MAX_PROMPT_LENGTH:
        print(f"Error: prompt too long ({len(prompt)} chars, max {MAX_PROMPT_LENGTH})")
        sys.exit(1)

    # Sanitize leading -- to prevent CLI flag injection
    prompt = prompt.lstrip()
    if prompt.startswith("--"):
        prompt = "- " + prompt

    data = load_tasks()
    if any(t["id"] == task_id for t in data["tasks"]):
        print(f"Error: task '{task_id}' already exists")
        sys.exit(1)

    if len(data["tasks"]) >= MAX_TASKS:
        print(f"Error: task limit reached ({MAX_TASKS}). Remove a task first.")
        sys.exit(1)

    data["tasks"].append({
        "id": task_id,
        "schedule": schedule,
        "prompt": prompt,
        "enabled": True,
        "last_run": None,
    })
    save_tasks(data)
    print(f"Added task '{task_id}' with schedule '{schedule}'")


def cmd_remove(args):
    if not args:
        print("Usage: cronctl.py remove <id>")
        sys.exit(1)

    task_id = args[0]
    data = load_tasks()
    original_len = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]

    if len(data["tasks"]) == original_len:
        print(f"Error: task '{task_id}' not found")
        sys.exit(1)

    save_tasks(data)
    print(f"Removed task '{task_id}'")


def cmd_enable(args):
    if not args:
        print("Usage: cronctl.py enable <id>")
        sys.exit(1)
    _set_enabled(args[0], True)


def cmd_disable(args):
    if not args:
        print("Usage: cronctl.py disable <id>")
        sys.exit(1)
    _set_enabled(args[0], False)


def _set_enabled(task_id, enabled):
    data = load_tasks()
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["enabled"] = enabled
            save_tasks(data)
            state = "enabled" if enabled else "disabled"
            print(f"Task '{task_id}' {state}")
            return
    print(f"Error: task '{task_id}' not found")
    sys.exit(1)


def cmd_run(args):
    """Run a task immediately (one-shot)."""
    if not args:
        print("Usage: cronctl.py run <id>")
        sys.exit(1)

    task_id = args[0]
    data = load_tasks()
    task = next((t for t in data["tasks"] if t["id"] == task_id), None)
    if not task:
        print(f"Error: task '{task_id}' not found")
        sys.exit(1)

    prompt = task["prompt"]
    print(f"Running task '{task_id}'...")
    result = subprocess.run(
        ["openclaude", "-p", prompt,
         "--dangerously-skip-permissions",
         "--output-format", "text"],
        capture_output=True, text=True, timeout=300
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    print(f"Exit code: {result.returncode}")

    # Update last_run
    now = datetime.now().isoformat()
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["last_run"] = now
            save_tasks(data)
            break


def cmd_log(args):
    if not args:
        print("Usage: cronctl.py log <id> [lines]")
        sys.exit(1)

    task_id = args[0]
    if not valid_task_id(task_id):
        print(f"Error: invalid task ID '{task_id}'")
        sys.exit(1)

    try:
        lines = int(args[1]) if len(args) > 1 else 20
        if lines < 1:
            raise ValueError
    except (ValueError, IndexError):
        print("Error: lines must be a positive integer")
        sys.exit(1)

    logfile = os.path.join(LOG_DIR, f"{task_id}.log")

    if not os.path.exists(logfile):
        print(f"No logs found for task '{task_id}'")
        return

    result = subprocess.run(["tail", "-n", str(lines), logfile], capture_output=True, text=True)
    print(result.stdout, end="")


def cmd_test(args):
    """Test a cron expression against the current time."""
    if not args:
        print("Usage: cronctl.py test <cron-expr>")
        sys.exit(1)

    expr = " ".join(args)
    fields = expr.split()
    if len(fields) != 5:
        print(f"Error: cron expression must have 5 fields, got {len(fields)}")
        sys.exit(1)

    now = datetime.now()
    print(f"Expression: {expr}")
    print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} (min={now.minute}, hour={now.hour}, dom={now.day}, month={now.month}, dow={now.weekday()})")

    parse_script = os.path.join(get_script_dir(), "cron-parse.sh")
    result = subprocess.run(
        ["bash", parse_script, expr],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Result: MATCH")
    else:
        print("Result: NO MATCH")


def _daemon_cmd(action):
    """Run a daemon command (start/stop/status)."""
    daemon_py = os.path.join(get_script_dir(), "cron_daemon.py")
    result = subprocess.run(
        [sys.executable, daemon_py, action],
        capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def cmd_start(_args):
    print("Starting daemon...")
    rc = _daemon_cmd("start")
    if rc == 0:
        print("Done. Use 'cronctl.py status' to check.")
    sys.exit(rc)


def cmd_stop(_args):
    print("Stopping daemon...")
    rc = _daemon_cmd("stop")
    if rc == 0:
        print("Done.")
    sys.exit(rc)


def cmd_status(_args):
    rc = _daemon_cmd("status")
    sys.exit(rc)


# ── Human-readable schedule parsing ────────────────────────────────────────────
NAMED_SCHEDULES = {
    "minutely":  "* * * * *",
    "hourly":    "0 * * * *",
    "daily":     "0 0 * * *",
    "weekly":    "0 0 * * 0",
    "monthly":   "0 0 1 * *",
    "yearly":    "0 0 1 1 *",
    "annually":  "0 0 1 1 *",
    "weekdays":  "0 9 * * 1-5",
    "weekends":  "0 9 * * 0,6",
}


def parse_human_schedule(text):
    """Try to parse human-readable schedule. Returns cron expr or None."""
    text = text.lower().strip()

    # Named schedules
    if text in NAMED_SCHEDULES:
        return NAMED_SCHEDULES[text]

    # "every N minutes/hours/days"
    import re
    m = re.match(r'every\s+(\d+)\s+(minute|hour|day)s?', text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "minute":
            if 60 % n != 0:
                return None  # doesn't divide evenly
            return f"*/{n} * * * *"
        elif unit == "hour":
            if 24 % n != 0:
                return None
            return f"0 */{n} * * *"
        elif unit == "day":
            return f"0 0 */{n} * *"

    # "at HH:MM"
    m = re.match(r'at\s+(\d{1,2}):(\d{2})', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{mi} {h} * * *"

    # "at Npm/am"
    m = re.match(r'at\s+(\d{1,2})(am|pm)', text)
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h != 12:
            h += 12
        if m.group(2) == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return f"0 {h} * * *"

    return None


def cmd_help(_args):
    print("""OpenClaude Cron — Persistent background task scheduler

USAGE
  cronctl.py <command> [args]

COMMANDS
  list                          Show all scheduled tasks
  add <id> <schedule> <prompt>  Add a task (schedule can be cron expr or human-readable)
  remove <id>                   Remove a task
  enable <id>                   Enable a task
  disable <id>                  Disable a task
  run <id>                      Execute a task now (one-shot)
  log <id> [lines]              View task logs (default: 20 lines)
  test <cron-expr>              Test a cron expression against current time
  start                         Start the background daemon
  stop                          Stop the background daemon
  status                        Check daemon status
  help                          Show this help

HUMAN-READABLE SCHEDULES (for 'add')
  "every 5 minutes"             → */5 * * * *
  "every 2 hours"               → 0 */2 * * *
  "daily"                       → 0 0 * * *
  "weekdays"                    → 0 9 * * 1-5
  "at 14:30"                    → 30 14 * * *
  "at 9am"                      → 0 9 * * *
  "weekly"                      → 0 0 * * 0

CRON EXPRESSION FORMAT
  ┌────── minute (0-59)
  │ ┌──── hour (0-23)
  │ │ ┌── day of month (1-31)
  │ │ │ ┌ month (1-12)
  │ │ │ │ ┌ day of week (0-7, 0=7=Sun)
  * * * * *

EXAMPLES
  */15 * * * *        Every 15 minutes
  0 9 * * 1-5         Weekdays at 9:00
  30 2 1,15 * *       2:30am on 1st and 15th
  0 0 * * 0           Every Sunday midnight
""")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "list": lambda: cmd_list(),
        "add": lambda: cmd_add(args),
        "remove": lambda: cmd_remove(args),
        "enable": lambda: cmd_enable(args),
        "disable": lambda: cmd_disable(args),
        "run": lambda: cmd_run(args),
        "log": lambda: cmd_log(args),
        "test": lambda: cmd_test(args),
        "start": lambda: cmd_start(args),
        "stop": lambda: cmd_stop(args),
        "status": lambda: cmd_status(args),
        "help": lambda: cmd_help(args),
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)

    commands[cmd]()


if __name__ == "__main__":
    main()
