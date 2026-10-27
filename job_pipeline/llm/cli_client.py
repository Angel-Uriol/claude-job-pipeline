"""Claude via the user's own logged-in Claude Code CLI (opt-in auth mode).

How it works: the Claude Agent SDK spawns the locally installed ``claude``
CLI, which authenticates with whatever login that CLI already has (``claude``
→ /login), or with a long-lived token from ``claude setup-token`` that the
user saved as ``CLAUDE_CODE_OAUTH_TOKEN`` in .env — we hand that token to the
child process only. This app never sees a password.

Terms note (also in the README and on the Setup page): Anthropic's Agent SDK
terms say third-party products should use API keys rather than offering
claude.ai login. This mode exists for people running this tool for
themselves, on their own machine, with their own Claude Code install. Do not
host this app for other people in this mode.

Two details borrowed from a pattern that is known to work:

* **Env scrubbing.** An ``ANTHROPIC_API_KEY`` in the environment outranks the
  CLI's login in non-interactive mode, and ``options.env`` *merges* with the
  parent env — so the only reliable fix is to pop those vars from this
  process before spawning the CLI.
* **No thinking, low effort, no tools, no settings.** These are mechanical
  JSON/text generations; defaults would make each call take minutes and load
  the user's personal CLAUDE.md files into context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMError, extract_json, tool_schema_for

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Vars that would divert the spawned CLI away from the user's own login,
# or leak a parent Claude Code session into the child.
STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
)

# One worker thread = calls are serialized (one CLI process at a time, gentle
# on subscription limits) and each runs in its own event loop, so this works
# from plain sync code AND from inside FastAPI's threadpool.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude-code")
_SCRUB_LOCK = threading.Lock()


def scrub_process_env() -> bool:
    """Remove login-shadowing vars from os.environ. Returns True if an API key
    was present (so the UI can mention it was ignored for this mode)."""
    with _SCRUB_LOCK:
        had_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        for k in STRIP_ENV:
            os.environ.pop(k, None)
        return had_key


def resolve_claude_cli(configured: str = "") -> str | None:
    """Find a standalone ``claude`` binary.

    Order: the configured path (CLAUDE_CLI_PATH), ``claude`` on PATH, the
    native installer's ~/.local/bin, the old ~/.claude/local install, the
    newest VS Code extension binary — and only then $CLAUDE_CODE_EXECPATH.
    That last one is set when this app is started from inside a Claude
    desktop/IDE session and points at that app's private copy, which usually
    can't log in on its own — so a real install always wins.
    """
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    for p in (Path.home() / ".local" / "bin" / "claude",
              Path.home() / ".claude" / "local" / "claude"):
        if p.exists():
            return str(p)
    ext_root = Path.home() / ".vscode" / "extensions"
    candidates = sorted(ext_root.glob("anthropic.claude-code-*/resources/native-binary/claude"))
    if candidates:
        return str(candidates[-1])
    env_path = os.environ.get("CLAUDE_CODE_EXECPATH", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    return None


class ClaudeCodeClient:
    """``ClaudeClient`` backed by the Claude Agent SDK + local Claude Code CLI."""

    auth_mode = "claude_code"

    def __init__(
        self, *, cli_path: str = "", oauth_token: str = "", max_attempts: int = 2
    ) -> None:
        try:
            import claude_agent_sdk  # noqa: F401  (import check only)
        except ImportError as e:  # pragma: no cover - dependency is declared
            raise LLMError("claude-agent-sdk is not installed (run `make install`).") from e
        self.api_key_was_present = scrub_process_env()
        self.cli_path = resolve_claude_cli(cli_path)
        if not self.cli_path:
            raise LLMError(
                "Claude Code CLI not found. Install it (https://claude.com/claude-code), "
                "run `claude` once to log in, or set CLAUDE_CLI_PATH in .env."
            )
        self._oauth_token = oauth_token.strip()
        self._max_attempts = max_attempts

    # -- internals ---------------------------------------------------------

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        assert self.cli_path
        cli_dir = str(Path(self.cli_path).parent)
        if cli_dir not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
        if self._oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self._oauth_token
        return env

    async def _run_async(self, prompt: str, model: str, system: str | None) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKError,
            CLINotFoundError,
            ResultMessage,
            TextBlock,
            ThinkingConfigDisabled,
            query,
        )

        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system,
            allowed_tools=[],
            max_turns=1,
            cli_path=self.cli_path,
            env=self._child_env(),
            setting_sources=[],
            effort="low",
            thinking=ThinkingConfigDisabled(type="disabled"),
        )
        parts: list[str] = []
        result: str | None = None
        is_error = False
        detail: str | None = None
        try:
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    parts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    is_error = bool(msg.is_error)
                    result = msg.result or result
                    if getattr(msg, "api_error_status", None) or getattr(msg, "errors", None):
                        detail = str(getattr(msg, "api_error_status", None)
                                     or getattr(msg, "errors", None))
        except CLINotFoundError as e:
            raise LLMError(f"Claude Code CLI not found: {e}") from e
        except ClaudeSDKError as e:
            raise LLMError(
                f"Claude Code call failed: {e}. Log the CLI in (run `claude`, then /login), "
                "or paste a token from `claude setup-token` on the Setup page."
            ) from e
        text = (result or "".join(parts)).strip()
        if is_error and not text:
            raise LLMError(detail or "Claude Code returned an error with no text.")
        return text

    def _run(self, prompt: str, model: str, system: str | None) -> str:
        future = _EXECUTOR.submit(asyncio.run, self._run_async(prompt, model, system))
        return future.result()

    # -- ClaudeClient ------------------------------------------------------

    def structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 4000,
        tool_name: str = "submit",
        tool_description: str = "Submit the structured answer.",
    ) -> T:
        _ = max_tokens  # the CLI manages its own output budget
        json_schema = json.dumps(tool_schema_for(schema))
        base = (
            f"{prompt}\n\n"
            f"({tool_description}) Instead of calling a tool, return ONLY one JSON "
            "object that conforms to this JSON Schema — no prose, no markdown fences.\n\n"
            f"JSON Schema:\n{json_schema}"
        )
        problem = ""
        for attempt in range(self._max_attempts):
            full = base if attempt == 0 else (
                f"{base}\n\nYour previous answer was INVALID: {problem}\n"
                "Return corrected JSON only."
            )
            raw = self._run(full, model, system)
            try:
                return schema.model_validate(extract_json(raw))
            except (ValueError, ValidationError) as e:
                problem = str(e)[:800]
                log.warning("Claude Code JSON invalid (attempt %d): %s", attempt + 1, problem)
        raise LLMError(f"Claude Code never returned valid JSON: {problem}")

    def text(
        self,
        prompt: str,
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2000,
    ) -> str:
        _ = max_tokens
        return self._run(prompt, model, system)


def describe_cli(cli_path: str = "", oauth_token: str = "") -> dict[str, Any]:
    """Cheap status for the Setup page (no model call)."""
    path = resolve_claude_cli(cli_path)
    return {
        "cli_found": bool(path),
        "cli_path": path,
        "oauth_token_env": bool(oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    }
