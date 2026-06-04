#!/usr/bin/env python3
"""
cron_json_helper.py — JSON operations for the cron daemon.
Replaces the fragile awk JSON parser with proper Python parsing.

Usage:
    cron_json_helper.py due-tasks <min> <hour> <dom> <month> <dow>
    cron_json_helper.py task-count
    cron_json_helper.py update-last-run <task-id>
"""
import json
import os
import sys

TASKS_FILE = os.path.expanduser("~/.openclaude/cron/cron-tasks.json")

# ── Cron field expansion (mirrors cron-parse.sh) ──────────────────────────────

def expand_field(field, lo, hi):
    """Expand a single cron field into a set of matching integers."""
    MAX_RANGE = 1000  # prevent memory DoS from absurd ranges like 0-999999999
    result = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue

        step = None
        range_part = part

        if "/" in part:
            range_part, step_str = part.rsplit("/", 1)
            step = int(step_str)
            if step < 1:
                raise ValueError(f"step must be >= 1, got {step}")

        if range_part == "*":
            vals = range(lo, hi + 1)
        elif "-" in range_part:
            rlo, rhi = range_part.split("-", 1)
            if not rlo.isdigit() or not rhi.isdigit():
                raise ValueError(f"invalid range endpoints: {range_part}")
            rlo, rhi = int(rlo), int(rhi)
            if rhi - rlo > MAX_RANGE:
                raise ValueError(f"range too large: {range_part} (max {MAX_RANGE} values)")
            vals = range(rlo, rhi + 1)
        else:
            vals = [int(range_part)]

        if step:
            step_vals = set(range(lo, hi + 1, step))
            result.update(v for v in vals if v in step_vals)
        else:
            result.update(vals)

    return result


def cron_matches(expr, cur_min, cur_hour, cur_dom, cur_month, cur_dow):
    """Check if a cron expression matches the given time."""
    fields = expr.split()
    if len(fields) != 5:
        return False

    f_min, f_hour, f_dom, f_month, f_dow = fields

    exp_min = expand_field(f_min, 0, 59)
    exp_hour = expand_field(f_hour, 0, 23)
    exp_dom = expand_field(f_dom, 1, 31)
    exp_month = expand_field(f_month, 1, 12)
    exp_dow = expand_field(f_dow, 0, 7)

    # Normalize DOW: 7 -> 0 (Sunday)
    if 7 in exp_dow:
        exp_dow.add(0)

    # Check minute, hour, month (must match)
    if cur_min not in exp_min:
        return False
    if cur_hour not in exp_hour:
        return False
    if cur_month not in exp_month:
        return False

    # DOM/DOW OR semantics (vixie-cron)
    dom_restricted = f_dom != "*"
    dow_restricted = f_dow != "*"

    if dom_restricted and dow_restricted:
        return cur_dom in exp_dom or cur_dow in exp_dow
    elif dom_restricted:
        return cur_dom in exp_dom
    elif dow_restricted:
        return cur_dow in exp_dow

    return True


# ── Task operations ────────────────────────────────────────────────────────────

def load_tasks():
    """Load tasks from JSON file. Returns empty list on any error."""
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE) as f:
            data = json.load(f)
        return data.get("tasks", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_tasks(tasks):
    """Save tasks atomically. Uses tempfile for crash safety."""
    import tempfile
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(TASKS_FILE), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump({"tasks": tasks}, f, indent=2)
        os.replace(tmp_path, TASKS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def cmd_due_tasks(args):
    """Output enabled tasks that are due right now."""
    if len(args) != 5:
        print("Usage: cron_json_helper.py due-tasks <min> <hour> <dom> <month> <dow>", file=sys.stderr)
        sys.exit(1)

    cur_min, cur_hour, cur_dom, cur_month, cur_dow = (int(a) for a in args)
    tasks = load_tasks()

    for task in tasks:
        if not task.get("enabled", True):
            continue
        try:
            is_due = cron_matches(task["schedule"], cur_min, cur_hour, cur_dom, cur_month, cur_dow)
        except (ValueError, IndexError) as e:
            print(f"WARN: skipping task '{task.get('id', '?')}' — bad cron expression: {e}", file=sys.stderr)
            continue
        if is_due:
            # Output: id|schedule|prompt (prompt may contain pipes — use \n as delimiter instead)
            task_id = task["id"]
            schedule = task["schedule"]
            prompt = task["prompt"]
            # Use tab delimiter since prompt can contain anything except tabs
            print(f"{task_id}\t{schedule}\t{prompt}")


def cmd_task_count(_args):
    """Output the number of tasks."""
    print(len(load_tasks()))


def cmd_update_last_run(args):
    """Update last_run timestamp for a task."""
    if len(args) != 1:
        print("Usage: cron_json_helper.py update-last-run <task-id>", file=sys.stderr)
        sys.exit(1)

    task_id = args[0]
    from datetime import datetime
    now = datetime.now().isoformat()

    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["last_run"] = now
            save_tasks(tasks)
            return

    print(f"Task '{task_id}' not found", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    cmd_args = sys.argv[2:]

    commands = {
        "due-tasks": cmd_due_tasks,
        "task-count": cmd_task_count,
        "update-last-run": cmd_update_last_run,
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)

    commands[cmd](cmd_args)


if __name__ == "__main__":
    main()
