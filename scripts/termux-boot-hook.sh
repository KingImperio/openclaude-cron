#!/data/data/com.termux/files/usr/bin/bash
# Termux:Boot hook — auto-start cron daemon after boot.
# Install: place in ~/.termux/boot/ and run:
#   mkdir -p ~/.termux/boot
#   cp termux-boot-hook.sh ~/.termux/boot/
#   chmod +x ~/.termux/boot/termux-boot-hook.sh
# Requires Termux:Boot app from F-Droid.

LOCK_FILE="${HOME}/.openclaude/cron/boot-hook.lock"
DAEMON_SCRIPT="${HOME}/.claude/skills/cron/scripts/cron_daemon.py"

# Prevent duplicate runs (boot hook can fire multiple times)
if [ -f "$LOCK_FILE" ]; then
    old_pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$old_pid" 2>/dev/null; then
        exit 0  # already running from a previous boot hook
    fi
fi
echo $$ > "$LOCK_FILE"

# Wait for storage to be ready (Android decrypt can be slow)
sleep 3

# Start daemon (silently — no-op if already running)
python3 "$DAEMON_SCRIPT" start 2>/dev/null

rm -f "$LOCK_FILE"
