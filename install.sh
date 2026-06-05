#!/usr/bin/env bash
set -euo pipefail

# ── OpenClade Cron — Installer ────────────────────────────────────────────────
# Usage:
#   ./install.sh              → local install (~/.claude/skills/cron)
#   ./install.sh --global     → system-wide (/usr/local/share/claude/skills/cron)
#   ./install.sh --uninstall  → remove the skill

SKILL_NAME="cron"
LOCAL_DIR="$HOME/.claude/skills/$SKILL_NAME"
GLOBAL_DIR="/usr/local/share/claude/skills/$SKILL_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    cat <<EOF
OpenClade Cron Installer

Usage:
  $0              Install locally to ~/.claude/skills/$SKILL_NAME
  $0 --global     Install system-wide to $GLOBAL_DIR
  $0 --uninstall  Remove the local installation
  $0 --uninstall --global  Remove the system-wide installation

Options:
  --help          Show this help
EOF
}

do_install() {
    local target="$1"
    local label="$2"

    echo "Installing OpenClade Cron → $target"

    if [ -L "$target" ] || [ -d "$target" ]; then
        echo "  Removing existing installation..."
        rm -rf "$target"
    fi

    mkdir -p "$(dirname "$target")"
    cp -r "$SCRIPT_DIR" "$target"
    chmod +x "$target/scripts/"*.py "$target/scripts/"*.sh 2>/dev/null || true

    echo "  Installed $(find "$target/scripts" -name '*.py' -o -name '*.sh' | wc -l) scripts"

    # Auto-configure permissions if openclaude settings exist
    local settings="$HOME/.openclaude/settings.json"
    if [ -f "$settings" ]; then
        if ! grep -q '"Bash(\*)"' "$settings" 2>/dev/null; then
            echo "  Note: Add \"Bash(*)\" to permissions.allow in $settings for non-interactive cron tasks"
        fi
    fi

    echo ""
    echo "Done! Use /cron in your next OpenClade session."
    echo "Or run directly: python3 $target/scripts/cronctl.py help"
}

do_uninstall() {
    local target="$1"
    local label="$2"

    if [ ! -d "$target" ] && [ ! -L "$target" ]; then
        echo "Nothing to uninstall at $target"
        return 0
    fi

    echo "Uninstalling OpenClade Cron from $target"
    rm -rf "$target"
    echo "Done."
}

# ── Parse args ────────────────────────────────────────────────────────────────
MODE="local"
ACTION="install"

for arg in "$@"; do
    case "$arg" in
        --global)   MODE="global" ;;
        --uninstall) ACTION="uninstall" ;;
        --help|-h)  usage; exit 0 ;;
        *)
            echo "Unknown option: $arg"
            usage
            exit 1
            ;;
    esac
done

if [ "$ACTION" = "uninstall" ]; then
    if [ "$MODE" = "global" ]; then
        do_uninstall "$GLOBAL_DIR" "system-wide"
    else
        do_uninstall "$LOCAL_DIR" "local"
    fi
else
    if [ "$MODE" = "global" ]; then
        do_install "$GLOBAL_DIR" "system-wide"
    else
        do_install "$LOCAL_DIR" "local"
    fi
fi
