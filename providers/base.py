"""Provider interface for Claude/Codex harness integrations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

DEFAULT_PROVIDER = "claude"


@dataclass(frozen=True)
class ProviderCapabilities:
    plan: bool = False
    interrupt: bool = False
    rewind: bool = False
    approval_ui: bool = False
    permission_ui: bool = False
    question_ui: bool = False
    todo_name: bool = False
    commit_on_done: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    error: str | None = None
    turns_delta: int = 0
    plan_pending: bool = False


SessionData = Mapping[str, Any]


class HarnessProvider:
    name = "base"
    display_name = "Base"
    badge = "??"
    capabilities = ProviderCapabilities()

    def session_payload(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "provider_label": self.display_name,
            "provider_badge": self.badge,
            "capabilities": self.capabilities.to_dict(),
        }

    def start_task(self, session: SessionData, prompt: str, query: str) -> ProviderResult:
        return ProviderResult(ok=False, error=f"{self.display_name} does not support task start")

    def complete_task(self, session: SessionData, *, no_commit: bool = False) -> ProviderResult:
        return ProviderResult(ok=True)

    def clear_session(self, session: SessionData) -> ProviderResult:
        return ProviderResult(ok=True)

    def approve_session(self, session: SessionData) -> ProviderResult:
        return ProviderResult(ok=False, error=f"Approval is not available for {self.display_name}")

    def confirm_permission(self, session: SessionData) -> ProviderResult:
        return ProviderResult(ok=False, error=f"Permission prompts are not available for {self.display_name}")

    def interrupt_session(self, session: SessionData) -> ProviderResult:
        return ProviderResult(ok=False, error=f"Interrupt is not available for {self.display_name}")

    def generate_todo_name(self, prompts: list[str]) -> str:
        return "Session work"

    def rewind_session(self, session: SessionData, total_turns: int, *, was_busy: bool = False) -> ProviderResult:
        return ProviderResult(ok=False, error=f"Rewind is not available for {self.display_name}")

    def is_session_busy(self, session: SessionData) -> bool | None:
        return None
