"""Codex provider adapter for the harness."""

from __future__ import annotations

from pane_io import is_pane_processing, send_to_pane

from .base import HarnessProvider, ProviderCapabilities, ProviderResult, SessionData


class CodexProvider(HarnessProvider):
    name = "codex"
    display_name = "Codex"
    badge = "CX"
    capabilities = ProviderCapabilities(commit_on_done=True)

    def start_task(self, session: SessionData, prompt: str, query: str) -> ProviderResult:
        if query.strip():
            return ProviderResult(
                ok=False,
                error="Plan mode and Claude-specific send options are not available for Codex sessions",
            )

        pane_idx = session.get("pane_index")
        if pane_idx and not send_to_pane(pane_idx, prompt, submit=True):
            return ProviderResult(ok=False, error="Failed to send to iTerm2")

        return ProviderResult(ok=True, turns_delta=1)

    def clear_session(self, session: SessionData) -> ProviderResult:
        pane_idx = session.get("pane_index")
        if pane_idx and not send_to_pane(pane_idx, "/new", submit=True):
            return ProviderResult(ok=False, error="Failed to start a fresh Codex conversation")
        return ProviderResult(ok=True)

    def complete_task(self, session: SessionData, *, no_commit: bool = False) -> ProviderResult:
        if no_commit:
            return ProviderResult(ok=True)

        pane_idx = session.get("pane_index")
        if pane_idx and not send_to_pane(
            pane_idx,
            "please create a git commit for the changes you just made",
            submit=True,
        ):
            return ProviderResult(ok=False, error="Failed to send commit request to Codex")

        return ProviderResult(ok=True, turns_delta=1)

    def generate_todo_name(self, prompts: list[str]) -> str:
        return ""

    def is_session_busy(self, session: SessionData) -> bool | None:
        pane_idx = session.get("pane_index")
        if not pane_idx:
            return None
        return is_pane_processing(pane_idx)
