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

### 1. Set up iTerm2 panes

Open iTerm2 and create a 3-column × 2-row pane layout (6 panes total). The harness expects panes numbered column-first (top-to-bottom, left-to-right), which is iTerm2's default indexing.

### 2. Start the harness

```bash
./launch_app.command
```

This starts the Python server on port **8420** and opens Chrome in app mode (fullscreen, no browser chrome). The app is available at `http://127.0.0.1:8420`.

To use a custom port:

```bash
./launch_app.command 9000
```

Server logs go to `/tmp/code_harness_<PORT>.log`.

### 3. Make sure Claude Code is running in each pane

Each pane should have the `claude` CLI already running and waiting for input. The harness sends keystrokes directly — it does not launch Claude itself.

---

## Using the UI

### Session Tabs

The top bar shows **6 session tabs**. Each tab corresponds to one iTerm2 pane. Tabs display live status:

- **Spinning indicator** — session is busy (Claude is working)
- **Glowing outline** — session is awaiting your approval (plan, permission, or question)
- Click a tab to switch to that session's view

### Task Management

Each session has a **backlog** of tasks. Tasks move through three states:

| State | Meaning |
|---|---|
| `backlog` | Queued, not yet sent to Claude |
| `in_progress` | Active — prompt has been sent to the pane |
| `done` | Completed and (optionally) committed |

**Creating a task** — type a prompt in the input box and press Enter or click **Add**.

**Starting a task** — click **Start** next to a backlog task. This sends the prompt to the pane. By default it uses plan mode (see below).

**Completing a task** — click **Done**. The harness switches the session to Haiku, sends a background commit prompt, then switches back to Opus. Pass `no_commit` to skip the commit.

**Reordering** — drag tasks up/down, or use the batch-reorder controls to move clusters.

### Start Options (query params)

When starting a task the UI can pass options that change how it's dispatched:

| Option | Effect |
|---|---|
| `?plan` | Enter plan mode — sends `/plan` first, then the prompt; waits for your approval |
| `?prefix=ultrathink` | Prepends `"ultrathink. "` to the prompt |
| `?prefix=team` | Appends the TeamCreate workflow instructions (spawns a multi-agent team) |
| `?model=opus` | Switches to a specific model before sending the plan |
| `?execmodel=sonnet` | Switches to a different model before executing (after plan approval) |

### Plan Approval

When a task is started in plan mode:

1. Claude enters `/plan`, generates a plan, and pauses.
2. The session tab glows — indicating it's awaiting approval.
3. Click **Approve Plan** in the UI (or **Reject** to cancel).
4. Approving sends Enter to the pane, and Claude begins execution.

### Permission & Question Dialogs

If Claude hits a bash permission prompt or an `AskUserQuestion`, the tab glows differently. Click **Yes** (or answer the question) from the web UI — the harness sends the keystroke to the pane.

### Interrupt & Rewind

- **Interrupt** — sends Escape to the pane; stops the current Claude turn.
- **Rewind** — opens Claude's `/rewind` UI and navigates to a specific checkpoint. Select how many turns back you want to go.

---

## Providers

Each session can use a different AI provider. Switch via the dropdown in the session header.

### Claude Code (`claude`)

Full-featured provider. Supports plan mode, interrupt, rewind, permission/question UI, and auto-commit. Uses three models across the task lifecycle:

- **Sonnet** — initial prompt / plan phase
- **Opus** — execution phase
- **Haiku** — background commit on task completion

### Codex (`codex`)

Legacy provider for OpenAI Codex. Sends the prompt directly to the pane without plan mode. Supports auto-commit only. Uses iTerm2's "is processing" flag for busy detection.

---

## Todo System

The **Todos** tab is a daily task list separate from the per-session backlog.

- Todos auto-roll over unchecked items to the next day (at midnight Pacific).
- Todos support indentation (sub-tasks).
- Checking a todo archives any linked session tasks.
- Archived tasks can be recalled per-todo.
- Each todo tracks elapsed time and can be linked to a session.
- Click **Generate Name** to have Haiku summarize a todo's tasks into a short title.

---

## Timers

The **Timers** tab shows:

- **Per-session timers** — elapsed time while a session is active/busy.
- **Unassigned master timer** — tracks time not attributed to a specific session.
- **Activity blocks** — Pomodoro-style work segments with history.
- **Daily history** — total tracked time per day.

Timers persist across restarts in `state.json`.

---

## State & Backups

All state is persisted to `state.json` in the project root (excluded from git). The harness backs up automatically:

- **Every 5 minutes** — rolling backup at `backups/state_latest.json`
- **Hourly** — timestamped snapshots at `backups/state_YYYYMMDD_HHMMSS.json`

Completed tasks are flushed to dated archive files in `archives/`.

---

## Configuration

There is no config file — all settings are either constants in `server.py` or set via the UI at runtime. Key constants you can edit:

| Constant | File | Default | Description |
|---|---|---|---|
| `PORT` | `server.py` | `8420` | HTTP server port (overridable via CLI arg) |
| `PANE_MAP` | `server.py` | `{1:1,2:3,3:5,4:2,5:4,6:6}` | Maps session number → iTerm2 pane index |
| `PACIFIC` | `server.py` | `-7` (PDT) | Timezone for daily rollover. Change to `-8` for PST |

To change the number of sessions or the pane layout, edit `PANE_MAP` and the `default_state()` function in `server.py`.

---

## Project Structure

```
claude-code-harness/
├── server.py              # HTTP server, REST API, state management
├── pane_io.py             # iTerm2 AppleScript helpers
├── index.html             # Full single-page web UI (PWA)
├── service-worker.js      # PWA offline support
├── manifest.webmanifest   # PWA metadata
├── icon.svg               # App icon
├── launch_app.command     # macOS launcher script
└── providers/
    ├── __init__.py        # Provider registry & factory
    ├── base.py            # Abstract HarnessProvider interface
    ├── claude.py          # Claude Code provider
    └── codex.py           # Codex (OpenAI) provider
```

---

## Adding a Custom Provider

1. Create `providers/your_provider.py` subclassing `HarnessProvider` from `providers/base.py`.
2. Set `name`, `display_name`, `badge`, and `capabilities`.
3. Implement `start_task()`, and optionally `complete_task()`, `interrupt_session()`, `rewind_session()`, etc.
4. Register it in `providers/__init__.py`.

---

## Troubleshooting

**Chrome not found**
The launcher looks for Chrome, Chrome Canary, and Chromium in `/Applications`. Install one of them or symlink.

**"Failed to send to iTerm2"**
- Confirm iTerm2 is running and has exactly 6 panes open.
- Confirm `osascript` is not blocked by macOS privacy settings (System Settings → Privacy & Security → Automation → Terminal → iTerm2).
- Run `POST /api/ping/<pane_index>` to test a specific pane.

**Port already in use**
Pass a different port: `./launch_app.command 9001`

**State looks wrong after a restart**
The harness resets all ephemeral state (busy, awaiting approval, etc.) on startup. Persistent state (tasks, todos, timers) comes from `state.json`. If `state.json` is corrupted, delete it — the harness creates a fresh default state on the next start.

---

## License

MIT
