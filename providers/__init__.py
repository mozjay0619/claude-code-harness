"""Provider registry for the multi-provider harness."""

from __future__ import annotations

from .base import DEFAULT_PROVIDER
from .claude import ClaudeProvider
from .codex import CodexProvider

_PROVIDERS = {
    DEFAULT_PROVIDER: ClaudeProvider(),
    "codex": CodexProvider(),
}


def provider_names() -> set[str]:
    return set(_PROVIDERS.keys())


def normalize_provider(name: str | None) -> str:
    if name in _PROVIDERS:
        return name
    return DEFAULT_PROVIDER


def get_provider(name: str | None):
    return _PROVIDERS[normalize_provider(name)]


def enrich_session(session_id: str, session_data: dict) -> dict:
    provider = get_provider(session_data.get("provider"))
    enriched = dict(session_data)
    enriched.update(provider.session_payload())
    enriched["id"] = session_id
    return enriched
