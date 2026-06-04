#!/usr/bin/env python3
"""
cronctl.py — Task management CLI for OpenClaude cron.

Usage:
    cronctl.py list                        List all tasks
    cronctl.py add <id> <schedule> <prompt> Add a task
    cronctl.py remove <id>                 Remove a task
    cronctl.py enable <id>                 Enable a task
    cronctl.py disable <id>                Disable a task
    cronctl.py log <id> [lines]            Show task logs
    cronctl.py test <cron-expr>            Test cron expression against current time
"""
import json
import os
import sys
import subprocess
import re
from datetime import datetime

TASKS_FILE = os.path.expanduser("~/.openclaude/cron/cron-tasks.json")
LOG_DIR = os.path.expanduser("~/.openclaude/cron/logs")


def ensure_file():
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    if not os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "w") as f:
            json.dump({"tasks": []}, f)


def load_tasks():
    ensure_file()
    with open(TASKS_FILE) as f:
        return json.load(f)


def save_tasks(data):
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, TASKS_FILE)


def cmd_list():
    data = load_tasks()
    tasks = data.get("tasks", [])
    if not tasks:
        print("No scheduled tasks.")
        return
    print(f"{'ID':<20} {'SCHEDULE':<20} {'ENABLED':<10} {'LAST RUN'}")
    print("-" * 75)
    for t in tasks:
        enabled = t.get("enabled", True)
        last_run = t.get("last_run") or "never"
        print(f"{t['id']:<20} {t['schedule']:<20} {str(enabled):<10} {last_run}")


def cmd_add(args):
    if len(args) < 3:
        print("Usage: cronctl.py add <id> <schedule> <prompt>")
        sys.exit(1)

    task_id, schedule, prompt = args[0], args[1], " ".join(args[2:])

    # Validate ID
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', task_id):
        print(f"Error: invalid task ID '{task_id}' (alphanumeric, hyphens, underscores, 1-64 chars)")
        sys.exit(1)

    # Validate schedule has 5 fields
    fields = schedule.split()
    if len(fields) != 5:
        print(f"Error: cron expression must have 5 fields, got {len(fields)}")
        sys.exit(1)

    data = load_tasks()
    if any(t["id"] == task_id for t in data["tasks"]):
        print(f"Error: task '{task_id}' already exists")
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


def cmd_log(args):
    if not args:
        print("Usage: cronctl.py log <id> [lines]")
        sys.exit(1)

    task_id = args[0]
    lines = int(args[1]) if len(args) > 1 else 20
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

    # Source the bash parser for testing
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parse_script = os.path.join(script_dir, "cron-parse.sh")
    result = subprocess.run(
        ["bash", parse_script, expr],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Result: MATCH")
    else:
        print("Result: NO MATCH")


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
        "log": lambda: cmd_log(args),
        "test": lambda: cmd_test(args),
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)

    commands[cmd]()


if __name__ == "__main__":
    main()
