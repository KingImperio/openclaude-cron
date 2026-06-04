---
name: cron
description: Schedule, manage, and monitor recurring background tasks that run via openclaude CLI. Use when the user asks to "schedule something", "set up a cron job", "run this every N minutes", "check scheduled tasks", or discusses recurring/periodic/automated task execution.
argument-hint: <command> [args...]
allowed-tools: [Bash, Read, Write, Glob, Grep]
---

# OpenClade Cron

Manage persistent, cross-session background tasks using standard cron schedules.
Tasks execute by shelling out to `openclaude -p` in the background.

## Interactive Mode

When `$ARGUMENTS` is empty, launch the interactive arrow-key menu:

```bash
python3 $HOME/.claude/skills/cron/scripts/cron-interactive.py
```

This opens a full-screen curses menu where the user can navigate with arrow keys and select actions. It handles all subcommands interactively — no need to remember argument syntax.

## Subcommands (direct mode)

When the user provides arguments, parse `$ARGUMENTS` and dispatch to the appropriate subcommand below.

### `list`
Show all configured tasks with their schedules, enabled status, and last run time.

```bash
bash ${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/cron}/scripts/cron-helpers.sh
source $HOME/.claude/skills/cron/scripts/cron-helpers.sh 2>/dev/null
python3 -c "
import json, os
f = os.path.expanduser('~/.openclaude/cron/cron-tasks.json')
data = json.load(open(f)) if os.path.exists(f) else {'tasks': []}
if not data['tasks']:
    print('No scheduled tasks.')
else:
    print(f'{'ID':<20} {'SCHEDULE':<20} {'ENABLED':<10} {'LAST RUN'}')
    print('-' * 70)
    for t in data['tasks']:
        print(f\"{t['id']:<20} {t['schedule']:<20} {str(t.get('enabled', True)):<10} {t.get('last_run', 'never')}\")
"
```

### `add <id> <schedule> <prompt>`
Add a new scheduled task.

- `id`: Alphanumeric + hyphens/underscores, 1-64 chars
- `schedule`: Standard 5-field cron expression (minute hour dom month dow)
- `prompt`: The prompt to pass to `openclaude -p`

Validate the schedule has exactly 5 fields. Validate the task ID is unique.

```bash
python3 -c "
import json, os, sys
f = os.path.expanduser('~/.openclaude/cron/cron-tasks.json')
os.makedirs(os.path.dirname(f), exist_ok=True)
data = json.load(open(f)) if os.path.exists(f) else {'tasks': []}
task_id = sys.argv[1]
schedule = sys.argv[2]
prompt = sys.argv[3]
# Check for duplicate
if any(t['id'] == task_id for t in data['tasks']):
    print(f'Error: task \"{task_id}\" already exists'); sys.exit(1)
data['tasks'].append({'id': task_id, 'schedule': schedule, 'prompt': prompt, 'enabled': True, 'last_run': None})
json.dump(data, open(f, 'w'), indent=2)
print(f'Added task \"{task_id}\" with schedule \"{schedule}\"')
" "$ARGUMENTS_ID" "$ARGUMENTS_SCHEDULE" "$ARGUMENTS_PROMPT"
```

### `remove <id>`
Remove a scheduled task by ID.

### `enable <id>` / `disable <id>`
Enable or disable a task without deleting it.

### `start`
Start the background daemon:

```bash
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh start
```

### `stop`
Stop the background daemon:

```bash
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh stop
```

### `status`
Check daemon status and task count:

```bash
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh status
```

### `logs <id> [lines]`
View recent logs for a task. Default: last 20 lines.

```bash
tail -n ${LINES:-20} $HOME/.openclaude/cron/logs/${TASK_ID}.log 2>/dev/null || echo "No logs found for task '${TASK_ID}'"
```

### `run <id>`
Immediately run a task (one-shot, doesn't wait for schedule):

```bash
python3 -c "
import json, os, sys, subprocess
f = os.path.expanduser('~/.openclaude/cron/cron-tasks.json')
data = json.load(open(f)) if os.path.exists(f) else {'tasks': []}
task_id = sys.argv[1]
task = next((t for t in data['tasks'] if t['id'] == task_id), None)
if not task:
    print(f'Task \"{task_id}\" not found'); sys.exit(1)
print(f'Running task \"{task_id}\"...')
result = subprocess.run(['openclaude', '-p', task['prompt'], '--dangerously-skip-permissions'], capture_output=True, text=True)
print(result.stdout)
if result.stderr: print(result.stderr, file=sys.stderr)
print(f'Exit code: {result.returncode}')
" "$ARGUMENTS_ID"
```

### `test <cron-expr>`
Test a cron expression against the current time:

```bash
source $HOME/.claude/skills/cron/scripts/cron-parse.sh "$1"
```

### `edit <id>`
Open an interactive editor to modify a task's schedule or prompt.

## Security Notes

- Tasks run with `--dangerously-skip-permissions`. Only schedule prompts you trust.
- The daemon runs as your user. Tasks have the same file access as your shell.
- Task prompts are stored in plain text in `~/.openclaude/cron/cron-tasks.json`.
- Logs may contain full AI output — be mindful of sensitive data.

## Files

| File | Purpose |
|---|---|
| `~/.openclaude/cron/cron-tasks.json` | Task definitions |
| `~/.openclaude/cron/cron.pid` | Daemon PID file |
| `~/.openclaude/cron/logs/*.log` | Per-task execution logs |
| `~/.claude/skills/cron/scripts/` | Implementation scripts |
