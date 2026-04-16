"""Claude Code provider adapter for the harness."""

from __future__ import annotations

import subprocess
import time
from urllib.parse import parse_qsl

from pane_io import send_enter_to_pane, send_keystroke_to_pane, send_to_pane

from .base import HarnessProvider, ProviderCapabilities, ProviderResult, SessionData

PREFIX_MAP = {
    "ultrathink": "ultrathink. ",
}

TEAM_SUFFIX = (
    "ultrathink. Delete any previous team with TeamDelete. "
    "Create an agent team to explore this, use the TeamCreate "
    "workflow instead of independent Explore agents. "
)

BACKGROUND_COMMIT_PROMPT = (
    "Use the Agent tool to spawn exactly one background haiku agent dedicated only to creating "
    "a git commit for the changes this session just made. Let that agent run in the background "
    "so the main conversation can continue, and do not wait on it unless it hits a safety issue. "
    "Its only job is to inspect git status and git diff, stage only the files it changed by "
    "explicit path, and create one concise commit using the repo's existing style such as "
    "'Feat:' or 'Fix:'. Do not delete, erase, clean up, or revert anything. Do not stage "
    "unrelated files from other sessions. Do not run git reset, git restore, git checkout, "
    "git clean, rebase, amend, cherry-pick, merge, or any force-push/history-rewriting command. "
    "If git shows conflicts, an in-progress merge, rebase, cherry-pick, or anything ambiguous, "
    "stop and report instead of trying to resolve it. Do not push. Keep tool use minimal and "
    "avoid anything that would trigger extra approvals beyond ordinary git status, git diff, "
    "git add, and git commit."
)


class ClaudeProvider(HarnessProvider):
    name = "claude"
    display_name = "Claude Code"
    badge = "CC"
    capabilities = ProviderCapabilities(
        plan=True,
        interrupt=True,
        rewind=True,
        approval_ui=True,
        permission_ui=True,
        question_ui=True,
        todo_name=True,
        commit_on_done=True,
    )

    def start_task(self, session: SessionData, prompt: str, query: str) -> ProviderResult:
        params = dict(parse_qsl(query, keep_blank_values=True))
        use_plan = "plan" in params
        prefix_text = PREFIX_MAP.get(params.get("prefix"), "")
        suffix_text = TEAM_SUFFIX if params.get("prefix") == "team" else ""
        plan_model = params.get("model")
        exec_model = params.get("execmodel")
        exec_switch = ""
        if exec_model and exec_model != plan_model:
            exec_switch = f"After the plan is approved and before executing, run /model {exec_model}\n\n"

        task_prompt = prompt
        if prefix_text:
            task_prompt = prefix_text + task_prompt
        if suffix_text:
            task_prompt = f"{task_prompt}\n\n{suffix_text}" if task_prompt else suffix_text

        pane_idx = session.get("pane_index")
        if pane_idx:
            if use_plan:
                if plan_model:
                    send_to_pane(pane_idx, f"/model {plan_model}")
                send_to_pane(pane_idx, "/plan")
                time.sleep(0.5)
            else:
                send_to_pane(pane_idx, "/model sonnet")

            if not send_to_pane(pane_idx, exec_switch + task_prompt):
                return ProviderResult(ok=False, error="Failed to send to iTerm2")

            if not use_plan:
                send_to_pane(pane_idx, "/model opus")

        return ProviderResult(ok=True, turns_delta=1, plan_pending=use_plan)

    def complete_task(self, session: SessionData, *, no_commit: bool = False) -> ProviderResult:
        if no_commit:
            return ProviderResult(ok=True)

        pane_idx = session.get("pane_index")
        if pane_idx:
            send_to_pane(pane_idx, "/model haiku")
            send_to_pane(pane_idx, "/effort medium")
            send_to_pane(pane_idx, BACKGROUND_COMMIT_PROMPT)
            send_to_pane(pane_idx, "/model opus")
            send_to_pane(pane_idx, "/effort auto")

        return ProviderResult(ok=True, turns_delta=1)

    def clear_session(self, session: SessionData) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if pane_idx and not send_to_pane(pane_idx, "/clear"):
            return ProviderResult(ok=False, error="Failed to clear Claude session")
        return ProviderResult(ok=True)

    def approve_session(self, session: SessionData) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if not pane_idx:
            return ProviderResult(ok=False, error="No pane for this session")
        if not send_enter_to_pane(pane_idx):
            return ProviderResult(ok=False, error="Failed to send approval")
        return ProviderResult(ok=True)

    def confirm_permission(self, session: SessionData) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if not pane_idx:
            return ProviderResult(ok=False, error="No pane for this session")
        if not send_enter_to_pane(pane_idx):
            return ProviderResult(ok=False, error="Failed to confirm permission prompt")
        return ProviderResult(ok=True)

    def interrupt_session(self, session: SessionData) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if not pane_idx:
            return ProviderResult(ok=False, error="No pane for this session")
        if not send_keystroke_to_pane(pane_idx, chr(27)):
            return ProviderResult(ok=False, error="Failed to send interrupt")
        return ProviderResult(ok=True)

    def generate_todo_name(self, prompts: list[str]) -> str:
        if not prompts:
            return "Unnamed work"
        prompt_list = "\n".join(f"- {p}" for p in prompts[-10:])
        query = (
            "Here are completed task prompts from a coding session:\n"
            f"{prompt_list}\n\n"
            "Generate a short (3-6 word) descriptive name summarizing the overall theme "
            "of this work. Reply with ONLY the name, nothing else."
        )
        try:
            result = subprocess.run(
                ["claude", "-p", query, "--model", "haiku"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, Exception):
            return "Session work"

        name = result.stdout.strip().strip('"').strip("'")
        if not name or result.returncode != 0:
            return "Session work"
        if len(name) > 60:
            return name[:57] + "..."
        return name

    def rewind_session(self, session: SessionData, total_turns: int, *, was_busy: bool = False) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if not pane_idx:
            return ProviderResult(ok=False, error="No pane for this session")

        if was_busy:
            send_keystroke_to_pane(pane_idx, chr(27))
            time.sleep(1.5)

        if not send_to_pane(pane_idx, "/rewind"):
            return ProviderResult(ok=False, error="Failed to open Claude rewind UI")
        time.sleep(1.5)

        if total_turns > 1:
            nav_chars = (chr(27) + "[B") * (total_turns - 1)
            if not send_keystroke_to_pane(pane_idx, nav_chars):
                return ProviderResult(ok=False, error="Failed to navigate Claude rewind UI")
        else:
            if not send_enter_to_pane(pane_idx):
                return ProviderResult(ok=False, error="Failed to select Claude rewind checkpoint")

        time.sleep(0.5)

        if not send_enter_to_pane(pane_idx):
            return ProviderResult(ok=False, error="Failed to confirm Claude rewind restore")
        time.sleep(2.0)

        if not send_keystroke_to_pane(pane_idx, chr(21)):
            return ProviderResult(ok=False, error="Failed to clear Claude input after rewind")

        return ProviderResult(ok=True)
