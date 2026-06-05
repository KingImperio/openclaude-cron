#!/usr/bin/env python3
"""
cron-interactive.py — Arrow-key interactive menu for OpenClaude cron.
Run without arguments for full interactive mode.
Run with arguments to dispatch directly (e.g., cron-interactive.py list).
"""
import curses
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CRONCTL = os.path.join(SCRIPT_DIR, "cronctl.py")
TASKS_FILE = os.path.expanduser("~/.openclaude/cron/cron-tasks.json")

# ── Menu Items ─────────────────────────────────────────────────────────────────
MENU_ITEMS = [
    ("list",     "List all scheduled tasks"),
    ("add",      "Add a new task"),
    ("edit",     "Edit a task"),
    ("remove",   "Remove a task"),
    ("enable",   "Enable a task"),
    ("disable",  "Disable a task"),
    ("start",    "Start background daemon"),
    ("stop",     "Stop background daemon"),
    ("status",   "Check daemon status"),
    ("logs",     "View task logs"),
    ("test",     "Test a cron expression"),
    ("suggest",  "Get cron expr from text"),
    ("run",      "Run a task now (one-shot)"),
]

# Actions that don't need additional input
NO_ARGS_ACTIONS = {"list", "start", "stop", "status"}

# Actions that need a task ID selection
NEEDS_TASK_ID = {"remove", "enable", "disable", "logs", "run", "edit"}


def run_cronctl(*args):
    """Run cronctl.py with given args and return output."""
    result = subprocess.run(
        [sys.executable, CRONCTL] + list(args),
        capture_output=True, text=True
    )
    return result.stdout + result.stderr


def get_task_schedules():
    """Read task IDs and schedules from cron-tasks.json."""
    if not os.path.exists(TASKS_FILE):
        return {}
    try:
        with open(TASKS_FILE) as f:
            data = json.load(f)
        return {t["id"]: t.get("schedule", "?") for t in data.get("tasks", [])}
    except (json.JSONDecodeError, OSError):
        return {}


def get_task_prompt(task_id):
    """Get the prompt for a specific task."""
    if not os.path.exists(TASKS_FILE):
        return None
    try:
        with open(TASKS_FILE) as f:
            data = json.load(f)
        for t in data.get("tasks", []):
            if t["id"] == task_id:
                return t.get("prompt")
    except (json.JSONDecodeError, OSError):
        pass
    return None


# ── Curses Menu ────────────────────────────────────────────────────────────────
class CursesMenu:
    def __init__(self, stdscr, title, items, subtitle=None):
        self.stdscr = stdscr
        self.title = title
        self.subtitle = subtitle
        self.items = items
        self.index = 0
        self.scroll_offset = 0
        self.result = None

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)

    def draw(self):
        self.stdscr.clear()
        h, w = self.stdscr.getmaxyx()

        title_str = f"  {self.title}  "
        self.stdscr.addstr(0, max(0, (w - len(title_str)) // 2), title_str,
                           curses.color_pair(2) | curses.A_BOLD)

        start_row = 1
        if self.subtitle:
            self.stdscr.addstr(1, 2, self.subtitle, curses.color_pair(4))
            start_row = 2

        self.stdscr.addstr(start_row, 0, "─" * (w - 1), curses.color_pair(3))
        start_row += 1

        max_visible = h - start_row - 3
        if max_visible < 1:
            max_visible = 1

        if self.index < self.scroll_offset:
            self.scroll_offset = self.index
        elif self.index >= self.scroll_offset + max_visible:
            self.scroll_offset = self.index - max_visible + 1

        for i in range(max_visible):
            item_idx = self.scroll_offset + i
            if item_idx >= len(self.items):
                break

            row = start_row + i
            item = self.items[item_idx]

            if isinstance(item, tuple):
                key, label = item
                display = f"  {key:<12} {label}"
            else:
                display = f"  {item}"

            try:
                if item_idx == self.index:
                    self.stdscr.addstr(row, 0, display.ljust(w - 1),
                                       curses.color_pair(1) | curses.A_BOLD)
                else:
                    self.stdscr.addstr(row, 0, display)
            except curses.error:
                pass  # Terminal too small, skip this row

        help_row = h - 2
        try:
            self.stdscr.addstr(help_row, 0, "─" * (w - 1), curses.color_pair(3))
            self.stdscr.addstr(help_row + 1, 2,
                               "↑/↓ navigate  Enter select  q/ESC quit",
                               curses.color_pair(4))
        except curses.error:
            pass

        self.stdscr.refresh()

    def run(self):
        self.draw()
        while True:
            try:
                key = self.stdscr.getch()
            except curses.error:
                return None

            if key == curses.KEY_UP or key == ord('k'):
                self.index = max(0, self.index - 1)
            elif key == curses.KEY_DOWN or key == ord('j'):
                self.index = min(len(self.items) - 1, self.index + 1)
            elif key == ord('\n') or key == curses.KEY_ENTER:
                self.result = self.items[self.index]
                return self.result
            elif key == ord('q') or key == 27:
                self.result = None
                return None
            elif key == ord('g'):
                self.index = 0
            elif key == ord('G'):
                self.index = len(self.items) - 1
            elif key == curses.KEY_RESIZE:
                pass  # Will redraw on next iteration

            self.draw()


class CursesPrompt:
    """Text input with curses."""
    def __init__(self, stdscr, prompt_text, default=""):
        self.stdscr = stdscr
        self.prompt_text = prompt_text
        self.value = default
        self.cursor_pos = len(default)

    def run(self):
        curses.curs_set(1)
        h, w = self.stdscr.getmaxyx()

        try:
            self.stdscr.addstr(h - 1, 0, " " * (w - 1))
            prompt_str = f"  {self.prompt_text}: "
            self.stdscr.addstr(h - 1, 0, prompt_str)
            self.stdscr.addstr(h - 1, len(prompt_str), self.value)
            self.stdscr.refresh()
        except curses.error:
            pass

        while True:
            try:
                key = self.stdscr.getch()
            except curses.error:
                curses.curs_set(0)
                return None

            if key == ord('\n') or key == curses.KEY_ENTER:
                curses.curs_set(0)
                return self.value.strip()
            elif key == 27:  # ESC
                curses.curs_set(0)
                return None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if self.cursor_pos > 0:
                    self.value = self.value[:self.cursor_pos - 1] + self.value[self.cursor_pos:]
                    self.cursor_pos -= 1
            elif 32 <= key <= 126 or key > 127:
                # Allow ASCII printable + extended characters
                try:
                    char = chr(key)
                    self.value = self.value[:self.cursor_pos] + char + self.value[self.cursor_pos:]
                    self.cursor_pos += 1
                except (ValueError, OverflowError):
                    pass

            # Redraw input line
            try:
                prompt_str = f"  {self.prompt_text}: "
                self.stdscr.addstr(h - 1, 0, " " * (w - 1))
                self.stdscr.addstr(h - 1, 0, prompt_str)
                # Truncate display if too long
                display_val = self.value
                max_input_w = w - len(prompt_str) - 1
                if max_input_w > 0 and len(display_val) > max_input_w:
                    display_val = "..." + display_val[-(max_input_w - 3):]
                self.stdscr.addstr(h - 1, len(prompt_str), display_val)
                self.stdscr.move(h - 1, len(prompt_str) + min(self.cursor_pos, max_input_w))
                self.stdscr.refresh()
            except curses.error:
                pass


def show_output(stdscr, title, output):
    """Show output in a scrollable view."""
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    lines = output.strip().split('\n') if output.strip() else ["(no output)"]
    max_lines = h - 3

    offset = 0
    while True:
        try:
            stdscr.clear()
            stdscr.addstr(0, 0, f"  {title}", curses.color_pair(2) | curses.A_BOLD)
            stdscr.addstr(1, 0, "─" * (w - 1), curses.color_pair(3))

            visible = lines[offset:offset + max_lines]
            for i, line in enumerate(visible):
                if len(line) > w - 2:
                    line = line[:w - 5] + "..."
                try:
                    stdscr.addstr(2 + i, 0, line)
                except curses.error:
                    break

            help_row = h - 1
            nav = "↑/↓ scroll  q/ESC back"
            if offset > 0:
                nav = "↑ scroll up  " + nav
            if offset + max_lines < len(lines):
                nav = "↓ scroll down  " + nav
            stdscr.addstr(help_row, 0, nav[:w-1], curses.color_pair(4))
            stdscr.refresh()
        except curses.error:
            pass

        try:
            key = stdscr.getch()
        except curses.error:
            return

        if key == ord('q') or key == 27:
            return
        elif key == curses.KEY_UP or key == ord('k'):
            offset = max(0, offset - 1)
        elif key == curses.KEY_DOWN or key == ord('j'):
            offset = min(max(0, len(lines) - max_lines), offset + 1)
        elif key == ord('g'):
            offset = 0
        elif key == ord('G'):
            offset = max(0, len(lines) - max_lines)


def interactive_menu(stdscr):
    """Main interactive flow."""
    while True:
        menu = CursesMenu(stdscr, "OpenClaude Cron", MENU_ITEMS,
                          subtitle="Select an action")
        choice = menu.run()

        if choice is None:
            return

        action = choice[0] if isinstance(choice, tuple) else choice

        # No-args actions: execute immediately
        if action in NO_ARGS_ACTIONS:
            output = run_cronctl(action)
            show_output(stdscr, f"cron {action}", output)
            continue

        # Need task ID selection
        if action in NEEDS_TASK_ID:
            tasks = get_task_schedules()
            if not tasks:
                show_output(stdscr, action, "No tasks configured. Add one first.")
                continue

            task_items = [(tid, f"schedule: {sched}") for tid, sched in tasks.items()]
            task_menu = CursesMenu(stdscr, f"Select task to {action}", task_items)
            task_choice = task_menu.run()

            if task_choice is None:
                continue

            task_id = task_choice[0]

            if action == "logs":
                prompt = CursesPrompt(stdscr, "Lines to show", "20")
                lines = prompt.run()
                if lines is None:
                    continue
                output = run_cronctl("log", task_id, lines)
                show_output(stdscr, f"Logs: {task_id}", output)
            elif action == "edit":
                sched_prompt = CursesPrompt(stdscr, "New schedule (leave empty to keep)", "")
                new_sched = sched_prompt.run()
                prompt_prompt = CursesPrompt(stdscr, "New prompt (leave empty to keep)", "")
                new_prompt = prompt_prompt.run()
                args = [task_id]
                if new_sched:
                    args += ["--schedule", new_sched]
                if new_prompt:
                    args += ["--prompt", new_prompt]
                if len(args) == 1:
                    show_output(stdscr, "Edit", "No changes specified.")
                else:
                    output = run_cronctl("edit", *args)
                    show_output(stdscr, f"Edit: {task_id}", output)
            elif action == "run":
                # Actually execute the task, not add a duplicate
                output = run_cronctl("run", task_id)
                show_output(stdscr, f"Running: {task_id}", output)
            else:
                output = run_cronctl(action, task_id)
                show_output(stdscr, f"{action}: {task_id}", output)
            continue

        # Add: needs id, schedule, prompt
        if action == "add":
            id_prompt = CursesPrompt(stdscr, "Task ID (alphanumeric/hyphens)")
            task_id = id_prompt.run()
            if task_id is None:
                continue

            sched_prompt = CursesPrompt(stdscr, "Cron schedule (5 fields)", "*/5 * * * *")
            schedule = sched_prompt.run()
            if schedule is None:
                continue

            prompt_prompt = CursesPrompt(stdscr, "Prompt for openclaude")
            prompt = prompt_prompt.run()
            if prompt is None:
                continue

            output = run_cronctl("add", task_id, schedule, prompt)
            show_output(stdscr, "Add task", output)
            continue

        # Test: needs cron expression
        if action == "test":
            expr_prompt = CursesPrompt(stdscr, "Cron expression", "* * * * *")
            expr = expr_prompt.run()
            if expr is None:
                continue
            output = run_cronctl("test", expr)
            show_output(stdscr, "Test expression", output)
            continue

        # Suggest: needs human-readable description
        if action == "suggest":
            desc_prompt = CursesPrompt(stdscr, "Description", "every 5 minutes")
            desc = desc_prompt.run()
            if desc is None:
                continue
            output = run_cronctl("suggest", desc)
            show_output(stdscr, "Suggest schedule", output)
            continue


def main():
    if len(sys.argv) > 1:
        result = run_cronctl(*sys.argv[1:])
        print(result, end="")
        return

    try:
        curses.wrapper(interactive_menu)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
