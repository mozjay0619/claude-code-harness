# Code Harness

A pane-based task orchestration UI for managing multiple concurrent [Claude Code](https://claude.ai/code) (and Codex) sessions in iTerm2 on macOS. Runs as a local web app (PWA) that lets you queue tasks, dispatch them to AI agents, approve plans, and track progress — all without touching the terminal.

![Dark theme UI with 6 session tabs, task lists, and timer panels]

---

## Overview

Code Harness maps your iTerm2 pane grid (3 columns × 2 rows = 6 panes) to numbered sessions. You write task prompts in the web UI, hit **Start**, and the harness sends the right Claude Code commands to the right pane via AppleScript. It tracks task states (backlog → in_progress → done), surfaces plan-approval and permission dialogs, and optionally auto-commits when a task is marked complete.

```
┌─────────────┬─────────────┬─────────────┐
│  Session 1  │  Session 2  │  Session 3  │
│  (Pane 1)   │  (Pane 3)   │  (Pane 5)   │
├─────────────┼─────────────┼─────────────┤
│  Session 4  │  Session 5  │  Session 6  │
│  (Pane 2)   │  (Pane 4)   │  (Pane 6)   │
└─────────────┴─────────────┴─────────────┘
```

---

## Requirements

| Requirement | Notes |
|---|---|
| macOS | AppleScript/osascript required |
| [iTerm2](https://iterm2.com) | Must be running with a 3×2 pane layout |
| Python 3 | Standard library only — no pip installs |
| Chrome, Chrome Canary, or Chromium | For the PWA window |
| [Claude Code CLI](https://claude.ai/code) | `claude` must be on your PATH (for Claude sessions) |

---

## Installation

```bash
git clone https://github.com/your-username/claude-code-harness.git
cd claude-code-harness
chmod +x launch_app.command
```

No dependencies to install — pure Python stdlib.

---

## Quick Start

1. Open iTerm2 and create a **3-column × 2-row** pane layout (6 panes total).
2. Start Claude Code in each pane — the harness sends keystrokes directly and does not launch it for you.
3. Run the harness:
   ```bash
   ./launch_app.command
   ```
   This opens the UI at `http://127.0.0.1:8420` in a Chrome app window.
4. To use a different port: `./launch_app.command 9000`

---

## Keyboard Shortcuts

### Tasks View

| Shortcut | Action |
|---|---|
| `Cmd+Enter` | Send focused backlog task to Claude; or mark focused in-progress task done |
| `Cmd+Shift+Enter` ×1 | Send with Sonnet plan |
| `Cmd+Shift+Enter` ×2 | Send with Opus plan → Sonnet execution |
| `Cmd+Shift+Enter` ×3 | Send with Opus plan + ultrathink |
| `Cmd+Shift+Enter` ×4+ | Send in team mode (multi-agent) |
| `Cmd+Esc` | Rewind focused in-progress item(s) back to backlog |
| `Enter` (outside textarea) | Approve pending plan |
| `Option+Enter` | Create new task slot in focused section |
| `Option+Backspace` | Delete focused backlog task |
| `↑ / ↓` | Navigate between items |
| `Option+↑ / Option+↓` | Navigate without clearing selection; at top backlog slot: browse prompt history |
| `Shift+↑ / Shift+↓` | Multi-select adjacent items |
| `Option+Shift+↑ / Option+Shift+↓` | Multi-select and reorder backlog items |
| `Option+↑ / Option+↓` (backlog, not top slot) | Reorder item up/down one position |

### Session Navigation

| Shortcut | Action |
|---|---|
| `Option+1` … `Option+6` | Switch to Session 1–6 directly |
| `Option+←` | Previous session |
| `Option+→` | Next session |
| `Option+Tab` | Toggle Tasks ↔ Todo view |

### Todo View

| Shortcut | Action |
|---|---|
| `↑ / ↓` | Navigate between todos |
| `Shift+↑ / Shift+↓` | Multi-select adjacent todos |
| `Option+↑ / Option+↓` | Reorder todo up/down |
| `Tab` | Indent (make sub-task) |
| `Shift+Tab` | Un-indent (promote to top-level) |
| `Cmd+Enter` | Recall archived tasks and switch to Tasks view |
| `Cmd+Backspace` | Clear todo text and unset session assignment |

### Textarea Editing

| Shortcut | Action |
|---|---|
| `Tab` | Insert 4-space soft tab |
| `Backspace` (after 4 spaces) | Delete soft tab |

---

## UI Buttons

| Button / Control | Action |
|---|---|
| Session tab | Click to switch sessions |
| `+` tab | Add a new session |
| Provider dropdown (in tab header) | Switch between Claude Code and Codex |
| **Approve** / **Yes** | Approve a pending plan or answer a permission dialog |
| **Stop** | Interrupt the active session |
| **Plan** toggle | Enable/disable plan mode |
| **Flush** | Archive all completed tasks |
| **Delete** | Remove the current session (requires no in-progress work) |
| Todo checkbox | Mark todo done/undone |

---

## Session Tab States

- **Spinning indicator** — session is busy (Claude is working)
- **Glowing outline** — session is awaiting your approval (plan, permission, or question)

---

## Troubleshooting

**Chrome not found**
The launcher looks for Chrome, Chrome Canary, and Chromium in `/Applications`. Install one of them or symlink.

**"Failed to send to iTerm2"**
- Confirm iTerm2 is running and has exactly 6 panes open.
- Confirm `osascript` is not blocked by macOS privacy settings (System Settings → Privacy & Security → Automation → Terminal → iTerm2).

**Port already in use**
Pass a different port: `./launch_app.command 9001`

---

## License

MIT
