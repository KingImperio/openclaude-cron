# OpenClaude Cron

A persistent, cross-session cron scheduler for [OpenClaude](https://github.com/Gitlawb/openclaude) (Claude Code). Runs background tasks by shelling out to `openclaude -p` on standard cron schedules.

Unlike the built-in `CronCreate`/`/loop` tools (which are session-scoped and expire after 7 days), OpenClaude Cron persists across session restarts and runs as an independent background daemon.

## Features

- **Persistent** — tasks survive session restarts (stored in `~/.openclaude/cron/cron-tasks.json`)
- **Standard cron syntax** — 5-field expressions with `*`, `,`, `-`, `/`
- **Background daemon** — runs independently, executes `openclaude -p` on each tick
- **Interactive menu** — arrow-key navigable curses UI when invoked with no arguments
- **Concurrency control** — max parallel tasks, skip-if-already-running
- **Auto-logging** — per-task logs with rotation
- **Zero dependencies** — pure Python/Bash, no pip install needed

## Install

One-liner — copies the skill directly into your OpenClaude skills directory:

```bash
git clone https://github.com/KingImperio/openclaude-cron.git ~/.claude/skills/cron
```

That's it. `/cron` is now available in your next OpenClaude session.

### Alternative: symlink (keeps repo updates separate)

```bash
git clone https://github.com/KingImperio/openclaude-cron.git ~/openclaude-cron
ln -s ~/openclaude-cron ~/.claude/skills/cron
```

Then update anytime with `cd ~/.claude/skills/cron && git pull`.

## Usage

### Interactive mode (arrow-key menu)

```
/cron
```

Opens a full-screen menu where you can navigate with arrow keys and select actions.

### Direct mode (slash command arguments)

```
/cron list                        — Show all scheduled tasks
/cron add my-task "*/5 * * * *" "check deploy status"
/cron remove my-task
/cron enable my-task
/cron disable my-task
/cron run my-task                 — Execute a task now (one-shot)
/cron start                       — Start background daemon
/cron stop                        — Stop daemon
/cron status                      — Check daemon status
/cron log my-task                 — View task logs
/cron test "*/15 * * * *"        — Test cron expression
```

### Standalone CLI (outside OpenClude)

```bash
python3 ~/.claude/skills/cron/scripts/cronctl.py list
python3 ~/.claude/skills/cron/scripts/cronctl.py add my-task "0 9 * * 1-5" "review open PRs"
python3 ~/.claude/skills/cron/scripts/cronctl.py run my-task
python3 ~/.claude/skills/cron/scripts/cronctl.py start  # note: daemon start uses cron-daemon.sh
```

## Architecture

```
~/.claude/skills/cron/
├── README.md
├── SKILL.md                        # Slash command definition
└── scripts/
    ├── cron-interactive.py         # Arrow-key curses menu
    ├── cronctl.py                  # Task management CLI
    ├── cron-daemon.sh              # Background scheduler daemon
    ├── cron-helpers.sh             # Shared utilities (logging, PID, locking)
    ├── cron-parse.sh               # POSIX 5-field cron expression parser
    └── cron_json_helper.py         # JSON operations for daemon (replaces awk)

~/.openclaude/cron/
├── cron-tasks.json                 # Persistent task config
├── cron.pid                        # Daemon PID file
└── logs/                           # Per-task execution logs
```

## How It Works

1. **Daemon** (`cron-daemon.sh`) runs in the background, checking every 60 seconds
2. On each tick, it calls `cron_json_helper.py due-tasks` to find enabled tasks matching the current time
3. Due tasks execute via `openclaude -p "<prompt>" --dangerously-skip-permissions`
4. Results are logged to `~/.openclaude/cron/logs/<task-id>.log`
5. Concurrency limit (default: 3) prevents resource exhaustion

## Configuration

Environment variables (optional):

| Variable | Default | Description |
|---|---|---|
| `CRON_TICK_INTERVAL` | `60` | Seconds between scheduler ticks |
| `CRON_TIMEOUT` | `300` | Max seconds per task execution |
| `CRON_MAX_CONCURRENT` | `3` | Max tasks running in parallel |
| `CRON_MAX_LOG_LINES` | `500` | Max lines per log file before rotation |

## Task Format

```json
{
  "tasks": [
    {
      "id": "backup-logs",
      "schedule": "*/5 * * * *",
      "prompt": "compress and archive /var/log/*",
      "enabled": true,
      "last_run": "2026-06-03T10:30:00"
    }
  ]
}
```

## Security Notes

- Tasks run with `--dangerously-skip-permissions`. Only schedule prompts you trust.
- The daemon runs as your user. Tasks have the same file access as your shell.
- Task prompts are stored in plain text in `~/.openclaude/cron/cron-tasks.json`.
- Logs may contain full AI output — be mindful of sensitive data.

## License

MIT
