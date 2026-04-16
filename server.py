#!/usr/bin/env python3
"""Task harness for pane-based Claude Code and Codex sessions."""

import copy
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from pane_io import ping_pane
from providers import DEFAULT_PROVIDER, enrich_session, get_provider, normalize_provider, provider_names

PACIFIC = timezone(timedelta(hours=-7))  # PDT; adjust to -8 for PST

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
session_busy = {}  # ephemeral busy state: {"1": True, "2": False, ...}
session_plan_pending = {}      # session_id → True when task started with plan mode
session_awaiting_approval = {} # session_id → True when plan delivered, waiting for user
session_awaiting_permission = {} # session_id → True when permission dialog shown (bash, etc.)
session_awaiting_question = {}   # session_id → True when AskUserQuestion used
session_subagent_count = {}      # session_id → int (active subagent count)
session_busy_pid = {}            # session_id → PID that set busy (for subagent filtering)
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
ARCHIVE_DIR = BASE_DIR / "archives"
HTML_FILE = BASE_DIR / "index.html"
MANIFEST_FILE = BASE_DIR / "manifest.webmanifest"
SERVICE_WORKER_FILE = BASE_DIR / "service-worker.js"
ICON_FILE = BASE_DIR / "icon.svg"

ARCHIVE_DIR.mkdir(exist_ok=True)

# iTerm2 indexes panes column-first (top-bottom, left-right)
# Map session number (reading order) to actual pane index:
# Session: 1=top-left  2=top-center  3=top-right
#          4=bot-left   5=bot-center   6=bot-right
# Pane:    1            3              5
#          2            4              6
PANE_MAP = {1: 1, 2: 3, 3: 5, 4: 2, 5: 4, 6: 6}


def default_state():
    tasks = []
    for i in range(1, 7):
        for _ in range(5):
            tasks.append({
                "id": str(uuid.uuid4())[:8],
                "prompt": "",
                "session": str(i),
                "state": "backlog",
                "created_at": datetime.now().isoformat(),
                "started_at": None,
                "completed_at": None,
            })
    pane_map = PANE_MAP
    return {
        "sessions": {
            str(i): {
                "label": f"Session {i}",
                "pane_index": pane_map[i],
                "provider": DEFAULT_PROVIDER,
            }
            for i in range(1, 7)
        },
        "tasks": tasks,
    }


def normalize_state(state):
    state.setdefault("sessions", {})
    state.setdefault("tasks", [])
    timers = state.setdefault("timers", {})
    timers.setdefault("date", datetime.now(PACIFIC).strftime("%Y-%m-%d"))
    timers.setdefault("sessions", {})
    timers.setdefault("todo_elapsed", {})
    timers.setdefault("todo_session_start", {})
    timers.setdefault("unassigned_master_elapsed", 0)
    timers.setdefault("activity_blocks", {})
    timers.setdefault("current_activity_block", None)
    timers.setdefault("busy_mode", False)
    timers.setdefault("history", {})
    session_ephemeral_keys = {
        "id",
        "busy",
        "awaiting_approval",
        "awaiting_permission",
        "awaiting_question",
        "subagent_count",
        "provider_label",
        "provider_badge",
        "capabilities",
    }
    for session in state["sessions"].values():
        for key in session_ephemeral_keys:
            session.pop(key, None)
        session["provider"] = normalize_provider(session.get("provider"))
    return state


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return normalize_state(json.load(f))
    # First run: create and persist default state so IDs are stable
    state = default_state()
    save_state(state)
    return state


BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)


def save_state(state):
    state = normalize_state(state)
    # Auto-backup: keep a rolling backup every 5 minutes
    if STATE_FILE.exists():
        backup_file = BACKUP_DIR / "state_latest.json"
        # Only backup if latest backup is older than 5 min or doesn't exist
        do_backup = True
        if backup_file.exists():
            age = datetime.now().timestamp() - backup_file.stat().st_mtime
            do_backup = age > 300
        if do_backup:
            import shutil
            shutil.copy2(STATE_FILE, backup_file)
            # Also keep a timestamped copy every hour
            hourly = BACKUP_DIR / datetime.now().strftime("state_%Y%m%d_%H.json")
            if not hourly.exists():
                shutil.copy2(STATE_FILE, hourly)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.rename(STATE_FILE)


def session_record(state, sess_id):
    if sess_id not in state["sessions"]:
        return None
    return enrich_session(sess_id, state["sessions"][sess_id])


def clear_ephemeral_state(sess_id):
    session_busy[sess_id] = False
    session_busy_pid.pop(sess_id, None)
    session_awaiting_approval.pop(sess_id, None)
    session_awaiting_permission.pop(sess_id, None)
    session_awaiting_question.pop(sess_id, None)
    session_subagent_count.pop(sess_id, None)
    session_plan_pending.pop(sess_id, None)


def flush_to_archive(state):
    now = datetime.now()
    filename = now.strftime("%Y-%m-%d_%H%M%S") + ".txt"
    filepath = ARCHIVE_DIR / filename

    lines = [f"=== CC Task Archive: {now.strftime('%Y-%m-%d %H:%M:%S')} ===\n"]

    # Group done tasks by session
    by_session = {}
    for task in state["tasks"]:
        if task["state"] == "done":
            sess = task.get("session") or "unassigned"
            by_session.setdefault(sess, []).append(task)

    for sess_id in sorted(by_session.keys()):
        sess_label = state["sessions"].get(str(sess_id), {}).get("label", f"Session {sess_id}")
        lines.append(f"\n## {sess_label}")
        lines.append("### Done")
        for task in by_session[sess_id]:
            ts = task.get("completed_at", "")
            if ts:
                ts = datetime.fromisoformat(ts).strftime("%H:%M")
            prompt_preview = task["prompt"].replace("\n", " ")[:200]
            lines.append(f"- [{ts}] {prompt_preview}")

    # Also include in-progress and backlog for completeness
    for label, st in [("In Progress", "in_progress"), ("Backlog", "backlog")]:
        remaining = [t for t in state["tasks"] if t["state"] == st]
        if remaining:
            lines.append(f"\n## {label}")
            for task in remaining:
                sess = task.get("session") or "?"
                prompt_preview = task["prompt"].replace("\n", " ")[:200]
                lines.append(f"- [Session {sess}] {prompt_preview}")

    with open(filepath, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Remove done tasks from state
    state["tasks"] = [t for t in state["tasks"] if t["state"] != "done"]
    save_state(state)
    return str(filepath)


def today_pacific():
    """Return today's date string in Pacific time."""
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


def ensure_today_todos(state, *, return_changed=False):
    """Ensure today's date exists in todos. Roll over unfinished items from previous days.
    Preserves topic hierarchy: if a topic has mixed checked/unchecked subtasks,
    the topic appears in both the old date (with checked subtasks) and today (with unchecked)."""
    changed = False
    if "todos" not in state:
        state["todos"] = {}
        changed = True

    today = today_pacific()

    # Find unfinished todos from previous days and move them to today
    moved = False
    for date_str in sorted(state["todos"].keys()):
        if date_str >= today:
            continue
        day_todos = state["todos"][date_str]

        # Parse into groups: [(topic_or_standalone, [subtasks])]
        groups = []
        i = 0
        while i < len(day_todos):
            item = day_todos[i]
            if (item.get("indent", 0) == 0 and
                i + 1 < len(day_todos) and
                day_todos[i + 1].get("indent", 0) > 0):
                # Topic with subtasks
                subtasks = []
                j = i + 1
                while j < len(day_todos) and day_todos[j].get("indent", 0) > 0:
                    subtasks.append(day_todos[j])
                    j += 1
                groups.append((item, subtasks))
                i = j
            else:
                groups.append((item, []))
                i += 1

        remaining = []  # stays in original date
        for topic, subtasks in groups:
            if not subtasks:
                # Standalone item
                if topic.get("done"):
                    remaining.append(topic)
                else:
                    state["todos"].setdefault(today, []).append(topic)
                    moved = True
                    changed = True
            else:
                checked = [s for s in subtasks if s.get("done")]
                unchecked = [s for s in subtasks if not s.get("done")]

                if not unchecked:
                    # All subtasks checked → keep entire group in original date
                    remaining.append(topic)
                    remaining.extend(subtasks)
                elif not checked:
                    # All subtasks unchecked → move entire group to today
                    state["todos"].setdefault(today, []).append(topic)
                    for s in subtasks:
                        state["todos"][today].append(s)
                    moved = True
                    changed = True
                else:
                    # Mixed: split group between old date and today
                    # Original date: topic (marked done) + checked subtasks
                    topic_copy_old = {**topic, "done": True}
                    remaining.append(topic_copy_old)
                    remaining.extend(checked)
                    # Today: new topic copy + unchecked subtasks
                    topic_copy_new = {
                        **topic,
                        "id": str(uuid.uuid4())[:8],
                        "done": False,
                    }
                    state["todos"].setdefault(today, []).append(topic_copy_new)
                    for s in unchecked:
                        state["todos"][today].append(s)
                    moved = True
                    changed = True

        state["todos"][date_str] = remaining

    # Ensure today exists with at least 10 slots
    if today not in state["todos"]:
        state["todos"][today] = []
        changed = True
    # Backfill indent field for existing todos missing it
    for date_str in state["todos"]:
        for todo in state["todos"][date_str]:
            if "indent" not in todo:
                todo["indent"] = 0
                changed = True

    while len([t for t in state["todos"][today] if not t.get("done")]) < 10:
        state["todos"][today].append({
            "id": str(uuid.uuid4())[:8],
            "text": "",
            "done": False,
            "session": None,
            "indent": 0,
            "created_at": datetime.now(PACIFIC).isoformat(),
        })
        changed = True

    if moved:
        # Clean up empty old dates
        state["todos"] = {k: v for k, v in state["todos"].items() if v}
        changed = True

    if return_changed:
        return state, changed
    return state


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logging

    def _send_file(self, file_path, content_type, status=200, cache_control="no-cache"):
        if not file_path.exists():
            self._send_error(404, "Not found")
            return
        content = file_path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(content))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        path = urlparse(self.path).path

        if path in {"/", "/index.html"}:
            self._send_file(HTML_FILE, "text/html; charset=utf-8")

        elif path == "/manifest.webmanifest":
            self._send_file(MANIFEST_FILE, "application/manifest+json; charset=utf-8")

        elif path == "/service-worker.js":
            self._send_file(SERVICE_WORKER_FILE, "application/javascript; charset=utf-8")

        elif path == "/icon.svg":
            self._send_file(ICON_FILE, "image/svg+xml")

        elif path == "/api/state":
            state = load_state()
            state, changed = ensure_today_todos(state, return_changed=True)
            if changed:
                save_state(state)
            for sid in list(state["sessions"].keys()):
                enriched = session_record(state, sid)
                provider = get_provider(enriched.get("provider"))
                busy = session_busy.get(sid, False)
                detected_busy = provider.is_session_busy(enriched)
                if detected_busy is not None:
                    busy = detected_busy
                enriched["busy"] = busy
                enriched["awaiting_approval"] = session_awaiting_approval.get(sid, False)
                enriched["awaiting_permission"] = session_awaiting_permission.get(sid, False)
                enriched["awaiting_question"] = session_awaiting_question.get(sid, False)
                enriched["subagent_count"] = session_subagent_count.get(sid, 0)
                state["sessions"][sid] = enriched
            self._send_json(state)

        else:
            self._send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/timers":
            body = self._read_body()
            state = load_state()
            existing_timers = state.get("timers", {})
            # Preserve history and persisted per-todo timers when updating current timers
            history = state.get("timers", {}).get("history", {})
            state["timers"] = {
                "date": body.get("date", datetime.now(PACIFIC).strftime("%Y-%m-%d")),
                "sessions": body.get("sessions", existing_timers.get("sessions", {})),
                "todo_elapsed": body.get("todo_elapsed", existing_timers.get("todo_elapsed", {})),
                "todo_session_start": body.get("todo_session_start", existing_timers.get("todo_session_start", {})),
                "unassigned_master_elapsed": body.get("unassigned_master_elapsed", existing_timers.get("unassigned_master_elapsed", 0)),
                "activity_blocks": body.get("activity_blocks", existing_timers.get("activity_blocks", {})),
                "current_activity_block": body.get("current_activity_block", existing_timers.get("current_activity_block")),
                "busy_mode": body.get("busy_mode", existing_timers.get("busy_mode", False)),
                "history": history,
            }
            save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/timers/history":
            body = self._read_body()
            state = load_state()
            if "timers" not in state:
                state["timers"] = {"date": today_pacific(), "sessions": {}, "todo_elapsed": {}, "todo_session_start": {}, "unassigned_master_elapsed": 0, "activity_blocks": {}, "current_activity_block": None, "busy_mode": False, "history": {}}
            if "todo_elapsed" not in state["timers"]:
                state["timers"]["todo_elapsed"] = {}
            if "todo_session_start" not in state["timers"]:
                state["timers"]["todo_session_start"] = {}
            if "unassigned_master_elapsed" not in state["timers"]:
                state["timers"]["unassigned_master_elapsed"] = 0
            if "activity_blocks" not in state["timers"]:
                state["timers"]["activity_blocks"] = {}
            if "current_activity_block" not in state["timers"]:
                state["timers"]["current_activity_block"] = None
            if "busy_mode" not in state["timers"]:
                state["timers"]["busy_mode"] = False
            if "history" not in state["timers"]:
                state["timers"]["history"] = {}
            state["timers"]["history"][body["date"]] = body["total"]
            save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/task":
            body = self._read_body()
            state = load_state()
            task = {
                "id": str(uuid.uuid4())[:8],
                "prompt": body.get("prompt", ""),
                "session": body.get("session"),
                "state": "backlog",
                "created_at": datetime.now().isoformat(),
                "started_at": None,
                "completed_at": None,
                "turns": 0,
            }
            before_id = body.get("before_id")
            after_id = body.get("after_id")
            if before_id:
                for idx, t in enumerate(state["tasks"]):
                    if t["id"] == before_id:
                        state["tasks"].insert(idx, task)
                        break
                else:
                    state["tasks"].append(task)
            elif after_id:
                for idx, t in enumerate(state["tasks"]):
                    if t["id"] == after_id:
                        state["tasks"].insert(idx + 1, task)
                        break
                else:
                    state["tasks"].append(task)
            else:
                state["tasks"].append(task)
            save_state(state)
            self._send_json(task, 201)

        elif path.startswith("/api/task/") and path.endswith("/start"):
            task_id = path.split("/")[3]
            query = parsed.query or ""
            state = load_state()
            for task in state["tasks"]:
                if task["id"] == task_id and task["state"] == "backlog":
                    if not task.get("session"):
                        self._send_json({"error": "Assign a session first"}, 400)
                        return
                    if not task["prompt"].strip():
                        self._send_json({"error": "Empty prompt"}, 400)
                        return
                    session = session_record(state, task["session"])
                    provider = get_provider(session.get("provider"))
                    result = provider.start_task(session, task["prompt"], query)
                    if not result.ok:
                        self._send_json({"error": result.error}, 400)
                        return
                    if result.plan_pending:
                        session_plan_pending[task["session"]] = True
                    task["state"] = "in_progress"
                    task["started_at"] = datetime.now().isoformat()
                    task["turns"] = result.turns_delta
                    save_state(state)
                    self._send_json(task)
                    return
            self._send_error(404, "Not found")

        elif path.startswith("/api/task/") and path.endswith("/done"):
            task_id = path.split("/")[3]
            no_commit = "no_commit" in (parsed.query or "")
            state = load_state()
            for task in state["tasks"]:
                if task["id"] == task_id and task["state"] == "in_progress":
                    session = session_record(state, task["session"])
                    provider = get_provider(session.get("provider"))
                    result = provider.complete_task(session, no_commit=no_commit)
                    if not result.ok:
                        self._send_json({"error": result.error}, 400)
                        return
                    task["state"] = "done"
                    task["completed_at"] = datetime.now().isoformat()
                    task["turns"] = task.get("turns", 0) + result.turns_delta
                    save_state(state)
                    self._send_json(task)
                    return
            self._send_error(404, "Not found")

        elif path == "/api/flush":
            body = self._read_body()
            session = body.get("session")
            state = load_state()
            if session:
                sess = session_record(state, session)
                if sess:
                    provider = get_provider(sess.get("provider"))
                    result = provider.clear_session(sess)
                    if not result.ok:
                        self._send_json({"error": result.error}, 400)
                        return
                before = len(state["tasks"])
                state["tasks"] = [t for t in state["tasks"] if t.get("session") != session]
                removed = before - len(state["tasks"])
                save_state(state)
                self._send_json({"count": removed})
            else:
                done_count = sum(1 for t in state["tasks"] if t["state"] == "done")
                if done_count == 0:
                    self._send_json({"error": "No done tasks to flush"}, 400)
                    return
                filepath = flush_to_archive(state)
                self._send_json({"archived": filepath, "count": done_count})

        elif path == "/api/generate-todo-name":
            body = self._read_body()
            prompts = body.get("prompts", [])
            if not prompts:
                self._send_json({"name": "Unnamed work"})
                return
            provider_name = body.get("provider")
            sess_id = body.get("session")
            if not provider_name and sess_id:
                state = load_state()
                if sess_id in state["sessions"]:
                    provider_name = state["sessions"][sess_id].get("provider")
            provider = get_provider(provider_name)
            self._send_json({"name": provider.generate_todo_name(prompts)})

        elif path == "/api/todo":
            # Create a new todo for today
            body = self._read_body()
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todo = {
                "id": str(uuid.uuid4())[:8],
                "text": body.get("text", ""),
                "done": False,
                "session": body.get("session"),
                "indent": body.get("indent", 0),
                "created_at": datetime.now(PACIFIC).isoformat(),
            }
            after_id = body.get("after_id")
            if after_id:
                for idx, t in enumerate(state["todos"][today]):
                    if t["id"] == after_id:
                        state["todos"][today].insert(idx + 1, todo)
                        break
                else:
                    state["todos"][today].append(todo)
            else:
                state["todos"][today].append(todo)
            save_state(state)
            self._send_json(todo, 201)

        elif path == "/api/task/reorder":
            body = self._read_body()
            task_id = body.get("task_id")
            direction = body.get("direction")  # -1 = up, 1 = down
            session = body.get("session")
            state = load_state()
            # Get backlog tasks for this session in array order
            bl_indices = [i for i, t in enumerate(state["tasks"])
                          if t.get("session") == session and t["state"] == "backlog"]
            # Find which position in bl_indices holds our task
            task_pos = None
            for pos, gi in enumerate(bl_indices):
                if state["tasks"][gi]["id"] == task_id:
                    task_pos = pos
                    break
            if task_pos is not None:
                swap_pos = task_pos + direction
                if 0 <= swap_pos < len(bl_indices):
                    gi_a = bl_indices[task_pos]
                    gi_b = bl_indices[swap_pos]
                    state["tasks"][gi_a], state["tasks"][gi_b] = state["tasks"][gi_b], state["tasks"][gi_a]
                    save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/task/batch-reorder":
            body = self._read_body()
            task_ids = set(body.get("task_ids", []))
            direction = body.get("direction")  # -1 = up, 1 = down
            session = body.get("session")
            state = load_state()
            bl_indices = [i for i, t in enumerate(state["tasks"])
                          if t.get("session") == session and t["state"] == "backlog"]
            # Find positions of selected tasks in backlog ordering
            selected_positions = sorted([pos for pos, gi in enumerate(bl_indices)
                                         if state["tasks"][gi]["id"] in task_ids])
            if selected_positions:
                min_pos = min(selected_positions)
                max_pos = max(selected_positions)
                is_contiguous = (max_pos - min_pos + 1) == len(selected_positions)

                if not is_contiguous:
                    # Non-contiguous: consolidate selected items into adjacent block
                    selected_set = set(selected_positions)
                    bl_tasks = [state["tasks"][gi] for gi in bl_indices]
                    selected_items = [bl_tasks[p] for p in selected_positions]
                    before = bl_tasks[:min_pos]
                    gap_unselected = [bl_tasks[i] for i in range(min_pos, max_pos + 1) if i not in selected_set]
                    after = bl_tasks[max_pos + 1:]
                    if direction == 1:
                        new_bl = before + gap_unselected + selected_items + after
                    else:
                        new_bl = before + selected_items + gap_unselected + after
                    # Write back into the global tasks array at the backlog positions
                    for pos, gi in enumerate(bl_indices):
                        state["tasks"][gi] = new_bl[pos]
                    save_state(state)
                elif direction == 1 and max_pos + 1 < len(bl_indices):
                    # Contiguous block move down
                    gi_src = bl_indices[max_pos + 1]
                    saved = state["tasks"][gi_src]
                    for pos in range(max_pos + 1, min_pos, -1):
                        state["tasks"][bl_indices[pos]] = state["tasks"][bl_indices[pos - 1]]
                    state["tasks"][bl_indices[min_pos]] = saved
                    save_state(state)
                elif direction == -1 and min_pos - 1 >= 0:
                    # Contiguous block move up
                    gi_src = bl_indices[min_pos - 1]
                    saved = state["tasks"][gi_src]
                    for pos in range(min_pos - 1, max_pos):
                        state["tasks"][bl_indices[pos]] = state["tasks"][bl_indices[pos + 1]]
                    state["tasks"][bl_indices[max_pos]] = saved
                    save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/todo/reorder":
            body = self._read_body()
            index = body.get("index")
            direction = body.get("direction")  # -1 = up, 1 = down
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todos = state["todos"].get(today, [])
            swap_idx = index + direction
            if 0 <= index < len(todos) and 0 <= swap_idx < len(todos):
                todos[index], todos[swap_idx] = todos[swap_idx], todos[index]
                state["todos"][today] = todos
                save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/todo/batch-reorder":
            body = self._read_body()
            indices = body.get("indices", [])
            direction = body.get("direction")  # -1 = up, 1 = down
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todos = state["todos"].get(today, [])
            positions = sorted(indices)
            if positions:
                min_pos = positions[0]
                max_pos = positions[-1]
                is_contiguous = (max_pos - min_pos + 1) == len(positions)

                if not is_contiguous:
                    # Non-contiguous: consolidate selected items into adjacent block
                    selected_set = set(positions)
                    selected_items = [todos[i] for i in positions]
                    before = todos[:min_pos]
                    gap_unselected = [todos[i] for i in range(min_pos, max_pos + 1) if i not in selected_set]
                    after = todos[max_pos + 1:]
                    if direction == 1:
                        # Cluster selected at bottom of range
                        todos_new = before + gap_unselected + selected_items + after
                    else:
                        # Cluster selected at top of range
                        todos_new = before + selected_items + gap_unselected + after
                    state["todos"][today] = todos_new
                    save_state(state)
                elif direction == 1 and max_pos + 1 < len(todos):
                    # Contiguous block move down
                    saved = todos[max_pos + 1]
                    for pos in range(max_pos + 1, min_pos, -1):
                        todos[pos] = todos[pos - 1]
                    todos[min_pos] = saved
                    state["todos"][today] = todos
                    save_state(state)
                elif direction == -1 and min_pos - 1 >= 0:
                    # Contiguous block move up
                    saved = todos[min_pos - 1]
                    for pos in range(min_pos - 1, max_pos):
                        todos[pos] = todos[pos + 1]
                    todos[max_pos] = saved
                    state["todos"][today] = todos
                    save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/todo/swap-clusters":
            body = self._read_body()
            rangeA = body.get("rangeA")  # [start, end] inclusive — must come first
            rangeB = body.get("rangeB")  # [start, end] inclusive — must come after A
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todos = state["todos"].get(today, [])
            aStart, aEnd = rangeA
            bStart, bEnd = rangeB
            before = todos[:aStart]
            clusterA = todos[aStart:aEnd + 1]
            between = todos[aEnd + 1:bStart]
            clusterB = todos[bStart:bEnd + 1]
            after = todos[bEnd + 1:]
            state["todos"][today] = before + clusterB + between + clusterA + after
            save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/todo/reposition":
            body = self._read_body()
            index = body.get("index")
            target_index = body.get("target_index")
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todos = state["todos"].get(today, [])
            if 0 <= index < len(todos) and 0 <= target_index < len(todos) and index != target_index:
                item = todos.pop(index)
                todos.insert(target_index, item)
                state["todos"][today] = todos
                save_state(state)
            self._send_json({"ok": True})

        elif path == "/api/todos/compact":
            # Reorder today's todos: non-empty first, empty last
            # But preserve indented (sub-todo) items in place relative to their topic
            state = load_state()
            state = ensure_today_todos(state)
            today = today_pacific()
            todos = state["todos"].get(today, [])
            # Only compact top-level empty items; leave indented items untouched
            non_empty = [t for t in todos if t["text"].strip() or t.get("indent", 0) > 0]
            empty = [t for t in todos if not t["text"].strip() and t.get("indent", 0) == 0]
            state["todos"][today] = non_empty + empty
            save_state(state)
            self._send_json({"ok": True})

        elif path.startswith("/api/todo/") and path.endswith("/toggle"):
            todo_id = path.split("/")[3]
            state = load_state()
            state = ensure_today_todos(state)
            state.setdefault("todo_archives", {})
            for date_str, todos in state["todos"].items():
                for todo in todos:
                    if todo["id"] == todo_id:
                        new_done = not todo["done"]
                        resp = dict(todo)

                        if new_done:
                            # Archiving: snapshot all tasks in this todo's session
                            sess = todo.get("session")
                            linked = [t for t in state["tasks"] if sess and t.get("session") == sess]
                            if linked:
                                state["todo_archives"][todo_id] = {
                                    "tasks": copy.deepcopy(linked),
                                    "archived_at": datetime.now().isoformat(),
                                    "original_session": sess,
                                }
                                state["tasks"] = [t for t in state["tasks"] if not (sess and t.get("session") == sess)]
                                resp["archived_count"] = len(linked)
                                resp["archived_session"] = sess
                        else:
                            # Uncheck: clear session, keep archive for manual recall via Cmd+Enter
                            todo["session"] = None
                            resp["session"] = None

                        todo["done"] = new_done
                        resp["done"] = new_done
                        save_state(state)
                        self._send_json(resp)
                        return
            self._send_error(404, "Not found")

        elif path.startswith("/api/todo/") and path.endswith("/recall"):
            todo_id = path.split("/")[3]
            state = load_state()
            state.setdefault("todo_archives", {})
            # Find the todo to get its current session
            target_session = None
            for date_str, todos in state.get("todos", {}).items():
                for todo in todos:
                    if todo["id"] == todo_id:
                        target_session = todo.get("session")
                        break
                if target_session is not None:
                    break
            if not target_session:
                self._send_json({"recalled_count": 0})
                return
            archive = state["todo_archives"].pop(todo_id, None)
            if not archive:
                self._send_json({"recalled_count": 0})
                return
            for task in archive["tasks"]:
                task["session"] = target_session
            state["tasks"].extend(archive["tasks"])
            save_state(state)
            self._send_json({"recalled_count": len(archive["tasks"]), "session": target_session})
            return

        elif path.startswith("/api/session/") and path.endswith("/approve"):
            sess_id = path.split("/")[3]
            state = load_state()
            sess = session_record(state, sess_id)
            if not sess:
                self._send_error(404, "Session not found")
                return
            provider = get_provider(sess.get("provider"))
            if not provider.capabilities.approval_ui:
                self._send_error(400, f"Approval UI is not available for {provider.display_name}")
                return
            result = provider.approve_session(sess)
            if result.ok:
                session_awaiting_approval.pop(sess_id, None)
                session_awaiting_permission.pop(sess_id, None)
                session_awaiting_question.pop(sess_id, None)
                session_busy[sess_id] = True
                # Increment turns for the most recent in_progress task on this session
                state = load_state()
                ip_tasks = [t for t in state["tasks"]
                            if t.get("session") == sess_id and t["state"] == "in_progress"]
                if ip_tasks:
                    latest = max(ip_tasks, key=lambda t: t.get("started_at", ""))
                    latest["turns"] = latest.get("turns", 0) + 1
                    save_state(state)
                print(f"[approve] sess={sess_id} plan approved from web app")
                self._send_json({"ok": True})
            else:
                self._send_json({"error": result.error}, 400)

        elif path.startswith("/api/session/") and path.endswith("/yes"):
            sess_id = path.split("/")[3]
            state = load_state()
            sess = session_record(state, sess_id)
            if not sess:
                self._send_error(404, "Session not found")
                return
            provider = get_provider(sess.get("provider"))
            if not provider.capabilities.permission_ui:
                self._send_error(400, f"Permission prompts are not available for {provider.display_name}")
                return
            result = provider.confirm_permission(sess)
            if result.ok:
                session_awaiting_permission.pop(sess_id, None)
                session_awaiting_question.pop(sess_id, None)
                session_busy[sess_id] = True
                print(f"[yes] sess={sess_id} permission approved from web app")
                self._send_json({"ok": True})
            else:
                self._send_json({"error": result.error}, 400)

        elif path.startswith("/api/session/") and path.endswith("/interrupt"):
            sess_id = path.split("/")[3]
            state = load_state()
            sess = session_record(state, sess_id)
            if not sess:
                self._send_error(404, "Session not found")
                return
            provider = get_provider(sess.get("provider"))
            result = provider.interrupt_session(sess)
            if not result.ok:
                self._send_json({"error": result.error}, 400)
                return
            session_busy[sess_id] = False
            self._send_json({"ok": True})

        elif path.startswith("/api/session/") and path.endswith("/status"):
            sess_id = path.split("/")[3]
            body = self._read_body()
            # PreToolUse hook sends awaiting_approval directly (plan mode)
            if body.get("awaiting_approval"):
                session_awaiting_approval[sess_id] = True
                session_busy[sess_id] = False
                print(f"[status] sess={sess_id} awaiting_approval=True (PreToolUse hook)")
                self._send_json({"ok": True})
                return
            # Notification hook sends awaiting_permission (bash/tool permission dialog)
            if body.get("awaiting_permission"):
                # Don't override plan approval (ExitPlanMode also triggers permission_prompt)
                if not session_awaiting_approval.get(sess_id):
                    session_awaiting_permission[sess_id] = True
                    session_busy[sess_id] = False
                    print(f"[status] sess={sess_id} awaiting_permission=True (Notification hook)")
                else:
                    print(f"[status] sess={sess_id} awaiting_permission ignored (awaiting_approval already set)")
                self._send_json({"ok": True})
                return
            # PreToolUse hook sends awaiting_question (AskUserQuestion)
            if body.get("awaiting_question"):
                session_awaiting_question[sess_id] = True
                print(f"[status] sess={sess_id} awaiting_question=True (PreToolUse hook)")
                self._send_json({"ok": True})
                return
            # SubagentStart: increment active subagent count
            if body.get("subagent_start"):
                session_subagent_count[sess_id] = session_subagent_count.get(sess_id, 0) + 1
                print(f"[status] sess={sess_id} subagent_start (count={session_subagent_count[sess_id]})")
                self._send_json({"ok": True})
                return
            # SubagentStop: decrement active subagent count
            if body.get("subagent_stop"):
                session_subagent_count[sess_id] = max(0, session_subagent_count.get(sess_id, 0) - 1)
                print(f"[status] sess={sess_id} subagent_stop (count={session_subagent_count[sess_id]})")
                self._send_json({"ok": True})
                return
            # Force idle from Stop hook — always honored, bypasses PID check
            if body.get("force_idle"):
                clear_ephemeral_state(sess_id)
                print(f"[status] sess={sess_id} force_idle (Stop hook)")
                self._send_json({"ok": True})
                return
            # Normal busy/idle from UserPromptSubmit/Stop hooks
            is_busy = body.get("busy", False)
            pid = body.get("pid")
            if is_busy:
                session_busy[sess_id] = True
                if pid:
                    session_busy_pid[sess_id] = pid
                # Any busy transition clears all awaiting states
                session_awaiting_approval.pop(sess_id, None)
                session_awaiting_permission.pop(sess_id, None)
                session_awaiting_question.pop(sess_id, None)
                session_plan_pending.pop(sess_id, None)
            else:
                # Only clear busy if same PID that set it (filters out subagent Stop events)
                stored_pid = session_busy_pid.get(sess_id)
                if stored_pid and pid and pid != stored_pid:
                    print(f"[status] sess={sess_id} ignoring busy=false from pid={pid} (busy set by pid={stored_pid})")
                    self._send_json({"ok": True})
                    return
                session_busy[sess_id] = False
                session_busy_pid.pop(sess_id, None)
                # Idle: clear approval and permission, but keep question (question IS the idle state)
                session_awaiting_approval.pop(sess_id, None)
                session_awaiting_permission.pop(sess_id, None)
            print(f"[status] sess={sess_id} busy={is_busy} pid={pid} awaiting_approval={session_awaiting_approval.get(sess_id)} awaiting_perm={session_awaiting_permission.get(sess_id)} awaiting_q={session_awaiting_question.get(sess_id)}")
            self._send_json({"ok": True})

        elif path.startswith("/api/session/") and path.endswith("/rewind"):
            sess_id = path.split("/")[3]
            body = self._read_body()
            task_ids = body.get("task_ids", [])
            total_turns = body.get("total_turns", 0)
            if not task_ids or total_turns <= 0:
                self._send_error(400, "task_ids and total_turns required")
                return
            state = load_state()
            sess = session_record(state, sess_id)
            if not sess:
                self._send_error(404, "Session not found")
                return
            provider = get_provider(sess.get("provider"))
            if not provider.capabilities.rewind:
                self._send_error(400, f"Rewind is not available for {provider.display_name}")
                return

            print(f"[rewind] sess={sess_id} tasks={task_ids} turns={total_turns}")
            result = provider.rewind_session(sess, total_turns, was_busy=session_busy.get(sess_id, False))
            if not result.ok:
                self._send_json({"error": result.error}, 400)
                return

            # Step 6: Move tasks back to backlog
            task_id_set = set(task_ids)
            rewound_tasks = []
            for task in state["tasks"]:
                if task["id"] in task_id_set:
                    task["state"] = "backlog"
                    task["started_at"] = None
                    task["completed_at"] = None
                    task["turns"] = 0
                    rewound_tasks.append(task)

            # Reposition: move rewound tasks to front of task list (top of backlog)
            # Keep them in chronological order (by original created_at)
            remaining = [t for t in state["tasks"] if t["id"] not in task_id_set]
            rewound_tasks.sort(key=lambda t: t.get("created_at", ""))
            state["tasks"] = rewound_tasks + remaining
            save_state(state)

            # Step 7: Clear ephemeral state
            clear_ephemeral_state(sess_id)

            print(f"[rewind] complete — {len(rewound_tasks)} tasks returned to backlog")
            self._send_json({"ok": True, "rewound": task_ids})

        elif path == "/api/session":
            # Create a new session
            state = load_state()
            existing_ids = [int(k) for k in state["sessions"].keys()]
            new_id = max(existing_ids) + 1 if existing_ids else 1
            new_id_str = str(new_id)
            # pane_index from map for sessions 1-6; None if beyond 6 panes (no iTerm2 pane)
            pane_index = PANE_MAP.get(new_id)
            state["sessions"][new_id_str] = {
                "label": f"Session {new_id}",
                "pane_index": pane_index,
                "provider": DEFAULT_PROVIDER,
            }
            # Create 5 default backlog tasks
            for _ in range(5):
                state["tasks"].append({
                    "id": str(uuid.uuid4())[:8],
                    "prompt": "",
                    "session": new_id_str,
                    "state": "backlog",
                    "created_at": datetime.now().isoformat(),
                    "started_at": None,
                    "completed_at": None,
                    "turns": 0,
                })
            save_state(state)
            self._send_json({"id": new_id_str, "session": session_record(state, new_id_str)}, 201)

        elif path.startswith("/api/ping/"):
            pane_idx = int(path.split("/")[-1])
            ok = ping_pane(pane_idx)
            self._send_json({"ok": ok, "pane": pane_idx})

        else:
            self._send_error(404, "Not found")

    def do_PUT(self):
        path = urlparse(self.path).path

        if path.startswith("/api/task/"):
            task_id = path.split("/")[3]
            body = self._read_body()
            state = load_state()
            for task in state["tasks"]:
                if task["id"] == task_id:
                    if "prompt" in body:
                        task["prompt"] = body["prompt"]
                    if "session" in body:
                        task["session"] = body["session"]
                    save_state(state)
                    self._send_json(task)
                    return
            self._send_error(404, "Not found")

        elif path.startswith("/api/todo/"):
            todo_id = path.split("/")[3]
            body = self._read_body()
            state = load_state()
            state = ensure_today_todos(state)
            for date_str, todos in state["todos"].items():
                for todo in todos:
                    if todo["id"] == todo_id:
                        if "text" in body:
                            todo["text"] = body["text"]
                        if "done" in body:
                            todo["done"] = body["done"]
                        if "indent" in body:
                            todo["indent"] = body["indent"]
                        if "session" in body:
                            old_session = todo.get("session")
                            new_session = body["session"]

                            if old_session != new_session:
                                if old_session is not None and new_session is not None:
                                    # A → B: move ALL tasks, tag with todo_id
                                    for task in state["tasks"]:
                                        if task.get("session") == old_session:
                                            task["session"] = new_session
                                            task["todo_id"] = todo["id"]
                                elif old_session is not None and new_session is None:
                                    # A → null: shelve ALL tasks
                                    for task in state["tasks"]:
                                        if task.get("session") == old_session:
                                            task["session"] = None
                                            task["todo_id"] = todo["id"]
                                elif old_session is None and new_session is not None:
                                    # null → B: unshelve ALL tasks tagged with this todo
                                    for task in state["tasks"]:
                                        if task.get("todo_id") == todo["id"] and task.get("session") is None:
                                            task["session"] = new_session

                            todo["session"] = new_session
                        save_state(state)
                        self._send_json(todo)
                        return
            self._send_error(404, "Not found")

        elif path.startswith("/api/session/"):
            sess_id = path.split("/")[-1]
            body = self._read_body()
            state = load_state()
            if sess_id in state["sessions"]:
                if "provider" in body:
                    requested = body["provider"]
                    if requested not in provider_names():
                        self._send_error(400, "Unsupported provider")
                        return
                    current_provider = state["sessions"][sess_id].get("provider", DEFAULT_PROVIDER)
                    if requested != current_provider:
                        ip_tasks = [
                            t for t in state["tasks"]
                            if t.get("session") == sess_id and t["state"] == "in_progress"
                        ]
                        if ip_tasks or session_busy.get(sess_id, False):
                            self._send_error(400, "Finish or flush in-progress work before changing provider")
                            return
                        clear_ephemeral_state(sess_id)
                        state["sessions"][sess_id]["provider"] = requested
                if "label" in body:
                    state["sessions"][sess_id]["label"] = body["label"]
                if "pane_index" in body:
                    state["sessions"][sess_id]["pane_index"] = body["pane_index"]
                save_state(state)
                self._send_json(session_record(state, sess_id))
                return
            self._send_error(404, "Not found")

        else:
            self._send_error(404, "Not found")

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/todo/"):
            todo_id = path.split("/")[3]
            state = load_state()
            state = ensure_today_todos(state)
            for date_str, todos in state["todos"].items():
                before = len(todos)
                state["todos"][date_str] = [t for t in todos if t["id"] != todo_id]
                if len(state["todos"][date_str]) < before:
                    save_state(state)
                    self._send_json({"ok": True})
                    return
            self._send_error(404, "Not found")

        elif path.startswith("/api/session/"):
            sess_id = path.split("/")[-1]
            state = load_state()
            if sess_id in state["sessions"]:
                del state["sessions"][sess_id]
                # Remove all tasks for this session
                state["tasks"] = [t for t in state["tasks"] if t.get("session") != sess_id]
                save_state(state)
                self._send_json({"ok": True})
                return
            self._send_error(404, "Not found")

        elif path.startswith("/api/task/"):
            task_id = path.split("/")[3]
            state = load_state()
            before = len(state["tasks"])
            state["tasks"] = [t for t in state["tasks"] if t["id"] != task_id]
            if len(state["tasks"]) < before:
                save_state(state)
                self._send_json({"ok": True})
                return
            self._send_error(404, "Not found")
        else:
            self._send_error(404, "Not found")


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    server = ReusableHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Code Harness running at http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
