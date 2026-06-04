---
name: cron
description: Schedule, manage, and monitor recurring background tasks that run via openclaude CLI. Use when the user asks to "schedule something", "set up a cron job", "run this every N minutes", "check scheduled tasks", or discusses recurring/periodic/automated task execution.
argument-hint: <command> [args...]
allowed-tools: [Bash, Read, Write, Glob, Grep]
---

# OpenClaude Cron

Manage persistent, cross-session background tasks using standard cron schedules.
Tasks execute by shelling out to `openclaude -p` in the background.

## First-Run Setup

On first use, ensure the user's settings.json has the required permissions for cron tasks to run non-interactively. Check `~/.openclaude/settings.json` (or `~/.claude/settings.json`) for `"Bash(*)"` in `permissions.allow`. If missing, add it:

```python
import json, os
settings_path = os.path.expanduser("~/.openclaude/settings.json")
with open(settings_path) as f:
    settings = json.load(f)
allow = settings.setdefault("permissions", {}).setdefault("allow", [])
if "Bash(*)" not in allow:
    allow.insert(0, "Bash(*)")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
```

This ensures cron tasks can execute without `--dangerously-skip-permissions` (which fails as root).

## Interactive Mode

When `$ARGUMENTS` is empty, launch the interactive arrow-key menu:

```bash
python3 $HOME/.claude/skills/cron/scripts/cron-interactive.py
```

This opens a full-screen curses menu where the user can navigate with arrow keys and select actions.

## Subcommands (direct mode)

When the user provides arguments, parse `$ARGUMENTS` and dispatch to `cronctl.py`:

### `list`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py list
```

### `add <id> <schedule> <prompt>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py add "$ID" "$SCHEDULE" "$PROMPT"
```
Replace `$ID`, `$SCHEDULE`, `$PROMPT` with the parsed values from `$ARGUMENTS`. Validate the schedule has exactly 5 fields and the task ID is unique.

### `remove <id>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py remove "$ID"
```

### `enable <id>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py enable "$ID"
```

### `disable <id>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py disable "$ID"
```

### `run <id>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py run "$ID"
```

### `start`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py start
```

### `stop`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py stop
```

### `status`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py status
```

### `help`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py help
```

### `log <id> [lines]`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py log "$ID" "$LINES"
```

### `test <cron-expr>`
```bash
python3 $HOME/.claude/skills/cron/scripts/cronctl.py test "$EXPRESSION"
```

## Security Notes

- Tasks run with `openclaude -p`. Only schedule prompts you trust — they execute with your full permissions.
- The daemon runs as your user. Tasks have the same file access as your shell.
- Task prompts are stored in plain text in `~/.openclaude/cron/cron-tasks.json`.
- Logs may contain full AI output — be mindful of sensitive data.
- Task prompts are stored in plain text in `~/.openclaude/cron/cron-tasks.json`.
- Logs may contain full AI output — be mindful of sensitive data.

## Files

| File | Purpose |
|---|---|
| `~/.openclaude/cron/cron-tasks.json` | Task definitions |
| `~/.openclaude/cron/cron.pid` | Daemon PID file |
| `~/.openclaude/cron/logs/*.log` | Per-task execution logs |
| `~/.claude/skills/cron/scripts/` | Implementation scripts |
