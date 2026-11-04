"""How much of the user's Claude subscription is used right now.

Only meaningful in "Claude Code login" mode (API-key users pay per token and
get a dollar estimate instead). We read the same numbers Claude Code's
`/usage` shows — 5-hour session and weekly utilization — from Anthropic's
OAuth usage endpoint.

⚠ That endpoint is UNDOCUMENTED and may change or refuse; every caller must
treat `available: False` as normal and fall back to estimates. We poll it at
most every CACHE_SECONDS, send a Claude Code user agent (other agents get
rate-limited), and never log or return the token.

Token sources, in order: the `claude setup-token` token saved in .env, then
(macOS) the Claude Code login stored in the Keychain.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import Any

import httpx

from .config import Secrets, load_secrets
from .llm.cli_client import resolve_claude_cli

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_SECONDS = 180
_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _keychain_token() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        token = json.loads(raw)["claudeAiOauth"]["accessToken"] if raw else None
        return str(token) if token else None
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None


def _cli_version(cli_path: str) -> str:
    cli = resolve_claude_cli(cli_path)
    if not cli:
        return "2.0.0"
    try:
        out = subprocess.run([cli, "--version"], capture_output=True, text=True,
                             timeout=10, check=False).stdout
        return out.split()[0] if out.strip() else "2.0.0"
    except (OSError, subprocess.TimeoutExpired):
        return "2.0.0"


def _window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("utilization") is None:
        return None
    used = float(raw["utilization"])
    return {"used_pct": round(used, 1), "left_pct": round(100 - used, 1),
            "resets_at": raw.get("resets_at")}


def get_plan_usage(secrets: Secrets | None = None, *, fresh: bool = False) -> dict[str, Any]:
    """{available, five_hour, seven_day, detail}. Never raises."""
    s = secrets or load_secrets()
    if s.auth_mode != "claude_code":
        return {"available": False, "detail": "API-key mode: billed per token, not a plan."}
    now = time.time()
    if not fresh and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return dict(_cache["value"])
    token = s.claude_code_oauth_token or _keychain_token()
    if not token:
        return {"available": False, "detail": "No Claude login token found to read usage."}
    try:
        resp = httpx.get(USAGE_URL, timeout=10, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": f"claude-code/{_cli_version(s.claude_cli_path)}",
            "Content-Type": "application/json",
        })
        if resp.status_code != 200:
            value = {"available": False,
                     "detail": f"Usage isn't readable right now (HTTP {resp.status_code})."}
        else:
            data = resp.json()
            value = {"available": True, "five_hour": _window(data.get("five_hour")),
                     "seven_day": _window(data.get("seven_day")), "detail": ""}
    except (httpx.HTTPError, ValueError) as e:
        log.info("plan usage unavailable: %s", e)
        value = {"available": False, "detail": "Usage isn't readable right now."}
    _cache.update(at=now, value=value)
    return dict(value)
