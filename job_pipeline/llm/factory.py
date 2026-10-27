"""Build the right ``ClaudeClient`` for this machine's auth mode."""

from __future__ import annotations

from typing import Any

from ..config import Secrets, load_secrets
from .base import ClaudeClient, LLMError


def build_llm(secrets: Secrets | None = None) -> ClaudeClient:
    """Return a client for the configured auth mode.

    Raises ``LLMError`` with a user-facing message when the mode can't work
    (no API key, Claude Code CLI not installed, ...).
    """
    s = secrets or load_secrets()
    if s.auth_mode == "claude_code":
        from .cli_client import ClaudeCodeClient

        return ClaudeCodeClient(cli_path=s.claude_cli_path)
    from .api_client import AnthropicAPIClient

    return AnthropicAPIClient(api_key=s.anthropic_api_key)


def llm_available(secrets: Secrets | None = None) -> bool:
    """Cheap check (no network): could `build_llm` succeed right now?"""
    s = secrets or load_secrets()
    if s.auth_mode == "claude_code":
        from .cli_client import resolve_claude_cli

        return resolve_claude_cli(s.claude_cli_path) is not None
    return bool(s.anthropic_api_key)


def self_check(model: str, secrets: Secrets | None = None) -> dict[str, Any]:
    """Make one tiny call end to end and report how auth went."""
    s = secrets or load_secrets()
    info: dict[str, Any] = {"auth_mode": s.auth_mode, "model": model, "ok": False, "detail": ""}
    try:
        client = build_llm(s)
        reply = client.text("Reply with exactly: OK", model=model, max_tokens=5)
        info["ok"] = "ok" in reply.lower()
        info["detail"] = "Connected to Claude." if info["ok"] else f"Unexpected reply: {reply!r}"
        if s.auth_mode == "claude_code" and getattr(client, "api_key_was_present", False):
            info["detail"] += " (An ANTHROPIC_API_KEY in the environment was ignored.)"
    except LLMError as e:
        info["detail"] = str(e)
    return info
