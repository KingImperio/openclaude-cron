---
name: cron
description: Schedule, manage, and monitor recurring background tasks that run via openclaude CLI. Use when the user asks to "schedule something", "set up a cron job", "run this every N minutes", "check scheduled tasks", or discusses recurring/periodic/automated task execution.
argument-hint: <command> [args...]
allowed-tools: [Bash, Read, Write, Glob, Grep]
---

# OpenClaude Cron

Manage persistent, cross-session background tasks using standard cron schedules.
Tasks execute by shelling out to `openclaude -p` in the background.

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
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh start
```

### `stop`
```bash
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh stop
```

### `status`
```bash
bash $HOME/.claude/skills/cron/scripts/cron-daemon.sh status
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
