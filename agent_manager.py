#!/usr/bin/env python3
"""
agent-manager: TUI dashboard for managing AI agent tmux sessions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Install:
    pip install "textual>=0.47.0"

Run:
    python agent_manager.py
    # or: chmod +x agent_manager.py && ./agent_manager.py

Config & notes are stored in:
    ~/.config/agent-manager/config.json
    ~/.config/agent-manager/notes.json
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)


# ── Config & Persistence ──────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "agent-manager"
NOTES_FILE = CONFIG_DIR / "notes.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict = {
    "refresh_interval": 3,
    "preview_lines": 80,
    "session_prefix": "",      # if set, only show sessions starting with this
    "tools": {
        "claude":    {"cmd": "claude",    "icon": "🤖"},
        "opencode":  {"cmd": "opencode",  "icon": "⚡"},
        "aider":     {"cmd": "aider",     "icon": "🔧"},
        "gemini":    {"cmd": "gemini",    "icon": "✨"},
        "shell":     {"cmd": "bash",      "icon": "🐚"},
    },
}


def ensure_config() -> Dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return DEFAULT_CONFIG.copy()


def load_notes() -> Dict[str, str]:
    if NOTES_FILE.exists():
        try:
            return json.loads(NOTES_FILE.read_text())
        except Exception:
            pass
    return {}


def save_notes(notes: Dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False))


# ── tmux Helpers ──────────────────────────────────────────────────────────────

def get_sessions(prefix: str = "") -> List[Dict]:
    """Return list of tmux sessions with metadata."""
    fmt = "|".join([
        "#{session_name}",
        "#{session_created}",
        "#{session_activity}",
        "#{session_windows}",
        "#{session_attached}",
    ])
    try:
        r = subprocess.run(
            ["tmux", "ls", "-F", fmt],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return []
    except Exception:
        return []

    sessions = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        name, created_ts, activity_ts, windows, attached = parts[:5]

        if prefix and not name.startswith(prefix):
            continue

        try:
            created_dt  = datetime.fromtimestamp(int(created_ts))
            activity_dt = datetime.fromtimestamp(int(activity_ts))
            idle_secs   = (datetime.now() - activity_dt).total_seconds()
        except Exception:
            created_dt = datetime.now()
            idle_secs  = 0.0

        sessions.append({
            "name":     name,
            "created":  created_dt,
            "idle":     idle_secs,
            "windows":  int(windows) if windows.isdigit() else 1,
            "attached": attached.strip() == "1",
        })

    return sorted(sessions, key=lambda s: s["idle"])


def capture_pane(session: str, lines: int = 80) -> str:
    """Capture plain-text output of the active pane in a session."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", f"-S-{lines}"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout if r.returncode == 0 else "(no output)"
    except Exception as e:
        return f"(error: {e})"


def tmux_new(name: str, cmd: str) -> Tuple[bool, str]:
    """Create a new detached tmux session and optionally run a command."""
    try:
        r = subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-x", "220", "-y", "50"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False, r.stderr.strip() or "failed"
        if cmd:
            subprocess.run(
                ["tmux", "send-keys", "-t", name, cmd, "Enter"],
                capture_output=True, timeout=5,
            )
        return True, ""
    except Exception as e:
        return False, str(e)


def tmux_kill(name: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "kill-session", "-t", name],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def tmux_rename(old: str, new: str) -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["tmux", "rename-session", "-t", old, new],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0, r.stderr.strip()
    except Exception as e:
        return False, str(e)


def tmux_send(session: str, keys: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "send-keys", "-t", session, keys, "Enter"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_idle(secs: float) -> str:
    s = int(secs)
    if s < 60:
        return f"{s}s"
    elif s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    elif s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    else:
        return f"{s // 86400}d{(s % 86400) // 3600}h"


def status_dot(sess: Dict) -> str:
    """Return a coloured markup dot for the session state."""
    if sess["attached"]:
        return "[bold green]●[/]"          # actively attached
    elif sess["idle"] < 15:
        return "[bold yellow]●[/]"         # very recently active
    elif sess["idle"] < 120:
        return "[blue]●[/]"                # recently active
    elif sess["idle"] < 600:
        return "[dim white]●[/]"           # idle
    else:
        return "[dim]●[/]"                 # sleeping / forgotten


# ── Modal: New Session ────────────────────────────────────────────────────────

class NewSessionModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: Dict) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        tools = list(self.config["tools"].items())
        options = [(f"{info['icon']}  {name}", name) for name, info in tools]
        first_val = tools[0][0] if tools else None

        yield Container(
            Label("◆  New Agent Session", id="modal-title"),
            Label("Task name"),
            Input(placeholder="e.g. refactor-auth, write-tests", id="inp-name"),
            Label("Tool"),
            Select(options, value=first_val, id="sel-tool"),
            Label("Custom command  (leave blank to use tool default)"),
            Input(placeholder="e.g. claude --model opus", id="inp-cmd"),
            Label("Note  (optional — what is this session for?)"),
            Input(placeholder="Rewriting auth module, deadline Friday", id="inp-note"),
            Horizontal(
                Button("Create  ↵", variant="primary", id="btn-create"),
                Button("Cancel  Esc", id="btn-cancel"),
                id="modal-buttons",
            ),
            id="new-session-modal",
        )

    def on_mount(self) -> None:
        self.query_one("#inp-name", Input).focus()

    @on(Button.Pressed, "#btn-create")
    @on(Input.Submitted, "#inp-name")
    def on_create(self) -> None:
        name  = self.query_one("#inp-name", Input).value.strip()
        tool  = str(self.query_one("#sel-tool", Select).value)
        cmd   = self.query_one("#inp-cmd",  Input).value.strip()
        note  = self.query_one("#inp-note", Input).value.strip()
        if not name:
            self.query_one("#inp-name", Input).focus()
            return
        tool_info = self.config["tools"].get(tool, {})
        effective_cmd = cmd or tool_info.get("cmd", "")
        full_name = f"{tool}-{name}" if tool not in ("shell", "bash") else name
        self.dismiss({"name": full_name, "cmd": effective_cmd, "note": note})

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)


# ── Modal: Confirm Kill ───────────────────────────────────────────────────────

class ConfirmKillModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def compose(self) -> ComposeResult:
        yield Container(
            Label("⚠  Kill Session", id="modal-title"),
            Static(
                f'Kill [bold]"{self.session_name}"[/bold]?\n\nAll processes in this session will be terminated.',
                id="confirm-body",
            ),
            Horizontal(
                Button("Kill  ↵", variant="error", id="btn-confirm"),
                Button("Cancel  Esc", id="btn-cancel"),
                id="modal-buttons",
            ),
            id="confirm-modal",
        )

    def on_mount(self) -> None:
        self.query_one("#btn-cancel", Button).focus()

    @on(Button.Pressed, "#btn-confirm")
    def on_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(False)


# ── Modal: Send Command ───────────────────────────────────────────────────────

class SendCommandModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def compose(self) -> ComposeResult:
        yield Container(
            Label(f"⌨  Send keys → [bold]{self.session_name}[/bold]", id="modal-title"),
            Input(placeholder="Command or text to send…", id="inp-cmd"),
            Horizontal(
                Button("Send  ↵", variant="primary", id="btn-send"),
                Button("Cancel  Esc", id="btn-cancel"),
                id="modal-buttons",
            ),
            id="send-modal",
        )

    def on_mount(self) -> None:
        self.query_one("#inp-cmd", Input).focus()

    @on(Button.Pressed, "#btn-send")
    @on(Input.Submitted, "#inp-cmd")
    def on_send(self) -> None:
        cmd = self.query_one("#inp-cmd", Input).value.strip()
        self.dismiss(cmd or None)

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)


# ── Modal: Rename ─────────────────────────────────────────────────────────────

class RenameModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        yield Container(
            Label(f"✎  Rename: {self.current}", id="modal-title"),
            Input(value=self.current, id="inp-name"),
            Horizontal(
                Button("Rename  ↵", variant="primary", id="btn-rename"),
                Button("Cancel  Esc", id="btn-cancel"),
                id="modal-buttons",
            ),
            id="rename-modal",
        )

    def on_mount(self) -> None:
        inp = self.query_one("#inp-name", Input)
        inp.focus()
        inp.action_end()

    @on(Button.Pressed, "#btn-rename")
    @on(Input.Submitted, "#inp-name")
    def on_rename(self) -> None:
        new = self.query_one("#inp-name", Input).value.strip()
        self.dismiss(new if new and new != self.current else None)

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)


# ── Modal: Note Editor ────────────────────────────────────────────────────────

class NoteModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, session_name: str, current_note: str = "") -> None:
        super().__init__()
        self.session_name = session_name
        self.current_note = current_note

    def compose(self) -> ComposeResult:
        yield Container(
            Label(f"📝  Note for: [bold]{self.session_name}[/bold]", id="modal-title"),
            Static("Ctrl+S to save  |  Esc to cancel", id="note-hint"),
            TextArea(self.current_note, id="note-area"),
            Horizontal(
                Button("Save  Ctrl+S", variant="primary", id="btn-save"),
                Button("Clear", variant="warning", id="btn-clear"),
                Button("Cancel  Esc", id="btn-cancel"),
                id="modal-buttons",
            ),
            id="note-modal",
        )

    def on_mount(self) -> None:
        self.query_one("#note-area", TextArea).focus()

    def action_save(self) -> None:
        text = self.query_one("#note-area", TextArea).text
        self.dismiss(text)

    @on(Button.Pressed, "#btn-save")
    def on_save(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#btn-clear")
    def on_clear(self) -> None:
        self.dismiss("")

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)


# ── CSS ───────────────────────────────────────────────────────────────────────

APP_CSS = """
/* ── Layout ── */
Screen { layout: vertical; }

#stats-bar {
    height: 1;
    background: $boost;
    color: $text-muted;
    padding: 0 2;
    dock: top;
}

#main-layout {
    height: 1fr;
}

/* Left panel */
#left-panel {
    width: 52;
    min-width: 40;
    border-right: solid $primary-darken-2;
    layout: vertical;
}

#filter-input {
    height: 3;
    border: solid $primary-darken-2;
    margin: 0;
}

#session-table {
    height: 1fr;
}

/* Right panel */
#right-panel {
    width: 1fr;
    layout: vertical;
}

#preview-header {
    height: 3;
    background: $boost;
    border-bottom: solid $primary-darken-2;
    padding: 0 1;
    content-align: left middle;
}

#note-banner {
    height: auto;
    max-height: 4;
    padding: 0 1;
    color: $warning;
    background: $panel-darken-1;
    border-bottom: dashed $primary-darken-3;
}

#preview-scroll {
    height: 1fr;
    overflow-y: auto;
}

#preview-content {
    padding: 0 1 1 1;
}

#status-bar {
    height: 1;
    background: $boost;
    color: $text-muted;
    padding: 0 2;
    dock: bottom;
}

/* ── Modals ── */
ModalScreen { align: center middle; }

#new-session-modal,
#confirm-modal,
#send-modal,
#rename-modal,
#note-modal {
    background: $surface;
    border: solid $primary;
    border-title-color: $primary;
    padding: 1 2 2 2;
    width: 62;
    height: auto;
    max-height: 90vh;
}

#modal-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
    padding-bottom: 1;
    border-bottom: solid $primary-darken-2;
    width: 100%;
}

#modal-buttons {
    margin-top: 1;
    height: auto;
    align: right middle;
}

#confirm-body {
    margin: 1 0;
    color: $warning;
}

#note-hint {
    color: $text-muted;
    text-style: italic;
    margin-bottom: 1;
}

#note-area {
    height: 10;
    margin-bottom: 1;
    border: solid $primary-darken-2;
}

/* ── Widgets ── */
Label {
    color: $text-muted;
    height: auto;
    margin-top: 1;
}

Input { margin-bottom: 0; }

Button { margin: 0 0 0 1; }

DataTable { height: 1fr; }

Select { margin-bottom: 0; }
"""


# ── Main App ──────────────────────────────────────────────────────────────────

class AgentManagerApp(App):
    """AI Agent tmux session manager TUI."""

    CSS = APP_CSS
    TITLE = "Agent Manager"
    SUB_TITLE = "tmux session dashboard"

    BINDINGS = [
        Binding("n",      "new_session",    "New",       show=True),
        Binding("enter",  "attach_session", "Attach",    show=True),
        Binding("k",      "kill_session",   "Kill",      show=True),
        Binding("r",      "rename_session", "Rename",    show=True),
        Binding("s",      "send_command",   "Send cmd",  show=True),
        Binding("e",      "edit_note",      "Note",      show=True),
        Binding("p",      "toggle_preview", "Preview",   show=True),
        Binding("f",      "focus_filter",   "Filter",    show=True),
        Binding("R",      "refresh",        "Refresh",   show=False),
        Binding("q",      "quit",           "Quit",      show=True),
    ]

    # Reactive state
    selected: reactive[Optional[str]]  = reactive(None)
    filter_q: reactive[str]            = reactive("")
    show_preview: reactive[bool]       = reactive(True)
    sessions: reactive[List[Dict]]     = reactive(list)
    status_msg: reactive[str]          = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self.config = ensure_config()
        self.notes  = load_notes()

    # ── Compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="stats-bar")

        with Horizontal(id="main-layout"):
            with Vertical(id="left-panel"):
                yield Input(placeholder="🔍 Filter sessions…", id="filter-input")
                yield DataTable(id="session-table", zebra_stripes=True, cursor_type="row")

            with Vertical(id="right-panel"):
                yield Static("Select a session", id="preview-header")
                yield Static("", id="note-banner")
                with ScrollableContainer(id="preview-scroll"):
                    yield Static("", id="preview-content")

        yield Static("", id="status-bar")
        yield Footer()

    # ── Mount ────────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        table = self.query_one("#session-table", DataTable)
        table.add_columns("", "Session", "Idle", "Win", "Created")

        self.action_refresh()
        interval = self.config.get("refresh_interval", 3)
        self.set_interval(interval, self._auto_refresh)
        self.set_interval(2, self._refresh_preview)

    # ── Refresh / Data ───────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        prefix = self.config.get("session_prefix", "")
        all_sessions = get_sessions(prefix)

        filt = self.filter_q.lower().strip()
        if filt:
            all_sessions = [s for s in all_sessions if filt in s["name"].lower()]

        self.sessions = all_sessions
        self._rebuild_table()
        self._update_stats(get_sessions(prefix))  # unfiltered for stats
        self._set_status(f"Refreshed {datetime.now().strftime('%H:%M:%S')}")

    def _rebuild_table(self) -> None:
        table = self.query_one("#session-table", DataTable)
        prev_selected = self.selected
        table.clear()

        for sess in self.sessions:
            dot      = status_dot(sess)
            idle     = fmt_idle(sess["idle"])
            created  = sess["created"].strftime("%m/%d %H:%M")
            wins     = str(sess["windows"])
            has_note = " 📝" if sess["name"] in self.notes else ""
            label    = sess["name"] + has_note
            table.add_row(dot, label, idle, wins, created, key=sess["name"])

        # Restore cursor position
        if prev_selected:
            for i, s in enumerate(self.sessions):
                if s["name"] == prev_selected:
                    table.move_cursor(row=i)
                    break

    def _update_stats(self, all_sessions: List[Dict]) -> None:
        total    = len(all_sessions)
        attached = sum(1 for s in all_sessions if s["attached"])
        active   = sum(1 for s in all_sessions if s["idle"] < 30)
        shown    = len(self.sessions)
        filt_info = f"  │  Filtered: {shown}" if self.filter_q else ""
        bar = f"  Sessions: {total}  │  Active (30s): {active}  │  Attached: {attached}{filt_info}"
        self.query_one("#stats-bar", Static).update(bar)

    # ── Preview ───────────────────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        if self.show_preview and self.selected:
            self._update_preview(self.selected)

    def _update_preview(self, name: str) -> None:
        sess_info = next((s for s in self.sessions if s["name"] == name), None)

        # Header line
        if sess_info:
            if sess_info["attached"]:
                state = "[bold green]● ATTACHED[/]"
            else:
                state = f"[dim]idle {fmt_idle(sess_info['idle'])}[/]"
            header_text = f"  📺  [bold]{name}[/]   {state}"
        else:
            header_text = f"  📺  [bold]{name}[/]"
        self.query_one("#preview-header", Static).update(header_text)

        # Note banner
        note = self.notes.get(name, "")
        note_widget = self.query_one("#note-banner", Static)
        if note:
            note_widget.display = True
            note_widget.update(f"  📝 {note[:300]}")
        else:
            note_widget.display = False

        # Pane content
        lines   = self.config.get("preview_lines", 80)
        content = capture_pane(name, lines)
        safe    = content.replace("[", "\\[").replace("]", "\\]")
        self.query_one("#preview-content", Static).update(safe)

    # ── Events ───────────────────────────────────────────────────────────────

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.selected = str(event.row_key.value)
            if self.show_preview:
                self._update_preview(self.selected)

    @on(Input.Changed, "#filter-input")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_q = event.value
        self.action_refresh()

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        def on_result(result: Optional[Dict]) -> None:
            if not result:
                return
            ok, err = tmux_new(result["name"], result["cmd"])
            if ok:
                if result.get("note"):
                    self.notes[result["name"]] = result["note"]
                    save_notes(self.notes)
                self._set_status(f"✓  Created: {result['name']}")
                self.action_refresh()
            else:
                self._set_status(f"✗  Error: {err}")

        self.push_screen(NewSessionModal(self.config), on_result)

    def action_attach_session(self) -> None:
        if not self.selected:
            self._set_status("No session selected")
            return
        self._attach_worker(self.selected)

    @work(exclusive=True)
    async def _attach_worker(self, session: str) -> None:
        async with self.suspend():
            subprocess.run(["tmux", "attach-session", "-t", session])

    def action_kill_session(self) -> None:
        if not self.selected:
            self._set_status("No session selected")
            return
        name = self.selected

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            ok = tmux_kill(name)
            if ok:
                if name in self.notes:
                    del self.notes[name]
                    save_notes(self.notes)
                self.selected = None
                self._set_status(f"✓  Killed: {name}")
                self.action_refresh()
            else:
                self._set_status(f"✗  Failed to kill: {name}")

        self.push_screen(ConfirmKillModal(name), on_confirm)

    def action_rename_session(self) -> None:
        if not self.selected:
            self._set_status("No session selected")
            return
        old = self.selected

        def on_rename(new_name: Optional[str]) -> None:
            if not new_name:
                return
            ok, err = tmux_rename(old, new_name)
            if ok:
                if old in self.notes:
                    self.notes[new_name] = self.notes.pop(old)
                    save_notes(self.notes)
                self.selected = new_name
                self._set_status(f"✓  Renamed: {old} → {new_name}")
                self.action_refresh()
            else:
                self._set_status(f"✗  Rename failed: {err}")

        self.push_screen(RenameModal(old), on_rename)

    def action_send_command(self) -> None:
        if not self.selected:
            self._set_status("No session selected")
            return
        name = self.selected

        def on_cmd(cmd: Optional[str]) -> None:
            if cmd:
                ok = tmux_send(name, cmd)
                self._set_status(f"✓  Sent to {name}" if ok else f"✗  Failed to send")

        self.push_screen(SendCommandModal(name), on_cmd)

    def action_edit_note(self) -> None:
        if not self.selected:
            self._set_status("No session selected")
            return
        name = self.selected
        current = self.notes.get(name, "")

        def on_note(text: Optional[str]) -> None:
            if text is None:           # cancelled
                return
            if text.strip():
                self.notes[name] = text.strip()
            else:
                self.notes.pop(name, None)
            save_notes(self.notes)
            self._update_preview(name)
            self._rebuild_table()      # refresh 📝 badge
            self._set_status("✓  Note saved")

        self.push_screen(NoteModal(name, current), on_note)

    def action_toggle_preview(self) -> None:
        self.show_preview = not self.show_preview
        self.query_one("#right-panel").display = self.show_preview
        self._set_status(f"Preview {'on' if self.show_preview else 'off'}")

    def action_focus_filter(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def action_quit(self) -> None:
        self.exit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(f"  {msg}")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not shutil.which("tmux"):
        print("Error: tmux not found in PATH. Please install tmux first.")
        sys.exit(1)

    app = AgentManagerApp()
    app.run()


if __name__ == "__main__":
    main()
