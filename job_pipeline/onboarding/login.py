"""One-click "Log in with Claude" for Claude Code mode.

Since 2026-11-04 the button runs the FULL sign-in, `claude auth login
--claudeai` (saved by the CLI itself, in the macOS Keychain): it allows model
calls AND Claude in Chrome. The older `claude setup-token` token only allows
model calls — Anthropic's browser bridge rejects it — so it's kept only as the
manual paste-a-token fallback. Notes below describe both flows.

`claude setup-token` is an interactive terminal program: it opens the browser
for the Claude login and, once you approve, PRINTS a long-lived token
(`sk-ant-oat…`). A web page can't see that terminal, so we run the program
ourselves inside a pseudo-terminal (it insists on a real TTY), watch its
output, and when the token appears we save it to .env and stop the process.
The Setup page polls `status()` and moves on by itself.

If the program asks for something (for example "paste the code shown in the
browser"), the page shows that prompt and forwards the answer via `send()`.

The token itself is never returned to the browser or logged — only a masked
preview.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..llm.cli_client import resolve_claude_cli
from .env_file import mask, set_env_values

# Token chars only, bounded on both sides so neighbouring words never get
# glued on (the CLI prints "Store this token securely…" right after it).
TOKEN_RE = re.compile(r"(?<![0-9A-Za-z_\-])sk-ant-oat[0-9A-Za-z_\-]{20,}(?![0-9A-Za-z_\-])")
# Cursor-forward (ESC[nC) is how terminal UIs draw spaces — keep it a space.
CURSOR_FWD_RE = re.compile(r"\x1b\[\d*C")
URL_RE = re.compile(r"https://[^\s\"'<>]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[@-_]")
TIMEOUT_SECONDS = 600
# A very wide virtual terminal so the token is printed on ONE line (no wrapping).
COLUMNS, ROWS = 400, 50


def _clean(text: str) -> str:
    text = CURSOR_FWD_RE.sub(" ", text)
    text = ANSI_RE.sub("", text).replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text)


@dataclass
class _Session:
    state: str = "idle"          # idle | running | done | error
    message: str = ""
    auth_url: str | None = None
    prompt: str | None = None    # last question the CLI is waiting on
    output: str = ""             # cleaned, token-free tail for the UI
    started_at: float = 0.0
    proc: subprocess.Popen[bytes] | None = None
    master_fd: int | None = None
    kind: str = "full"           # "full" (`claude auth login`) | "token" (setup-token)
    cli: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_S = _Session()


def _redact(text: str) -> str:
    return TOKEN_RE.sub(lambda m: mask(m.group(0)), text)


def _finish(state: str, message: str) -> None:
    _S.state, _S.message, _S.prompt = state, message, None
    if _S.proc and _S.proc.poll() is None:
        _S.proc.terminate()
    if _S.master_fd is not None:
        try:
            os.close(_S.master_fd)
        except OSError:
            pass
        _S.master_fd = None


def _reader() -> None:
    raw = ""
    fd = _S.master_fd
    assert fd is not None
    while True:
        if time.time() - _S.started_at > TIMEOUT_SECONDS:
            with _S.lock:
                _finish("error", "Timed out waiting for the login. Try again.")
            return
        try:
            ready, _, _ = select.select([fd], [], [], 0.5)
        except (OSError, ValueError):
            break
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            raw += chunk.decode("utf-8", errors="replace")
            text = _clean(raw)
            if _S.kind == "full" and "login successful" in text.lower():
                with _S.lock:
                    _finish_full_login()
                return
            # Search line by line — never join lines (that glued the next
            # line's words onto the token). The pty is 400 columns wide, so
            # the token is always printed on a single line.
            token = TOKEN_RE.search(text)
            with _S.lock:
                if token:
                    set_env_values({
                        "JOB_PIPELINE_AUTH_MODE": "claude_code",
                        "CLAUDE_CODE_OAUTH_TOKEN": token.group(0),
                    })
                    _finish("done", f"Logged in — token {mask(token.group(0))} saved to .env.")
                    return
                url = URL_RE.findall(text)
                if url:
                    _S.auth_url = url[-1]
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                last = lines[-1] if lines else ""
                _S.prompt = last if re.search(r"(paste|enter|code)\b.*[:?>]?$", last, re.I) \
                    else None
                _S.output = _redact(text)[-1500:]
        elif _S.proc and _S.proc.poll() is not None:
            break
    with _S.lock:
        if _S.state == "running":
            if _S.kind == "full":
                _finish_full_login()
            else:
                _finish("error",
                        "The login ended without a token. Check the output and try again.")


def _finish_full_login() -> None:
    """`claude auth login` ended: confirm with `claude auth status`, then use it."""
    from ..llm.cli_client import cli_account, forget_cli_account

    forget_cli_account()
    account = cli_account(_S.cli, fresh=True)
    if not account["logged_in"]:
        _finish("error", "The sign-in didn't finish. Try again.")
        return
    # The full sign-in replaces any older setup token (which would take
    # precedence over it and can't use Chrome).
    set_env_values({"JOB_PIPELINE_AUTH_MODE": "claude_code", "CLAUDE_CODE_OAUTH_TOKEN": ""})
    who = f" as {account['email']}" if account["email"] else ""
    _finish("done", f"Signed in{who}.")


def start(cli_path: str = "", kind: str = "full") -> dict[str, Any]:
    """Start the sign-in in a pseudo-terminal (no-op if running).

    kind="full" runs `claude auth login --claudeai` (default; works for
    Chrome), kind="token" runs `claude setup-token`.
    """
    with _S.lock:
        if _S.state == "running":
            return status()
        if sys.platform == "win32":
            _S.state, _S.message = "error", (
                "One-click login isn't available on Windows yet. Open a terminal, run "
                "`claude auth login`, finish the sign-in, then click Save & test connection."
            )
            return status()
        cli = resolve_claude_cli(cli_path)
        if not cli:
            _S.state, _S.message = "error", "Install the Claude Code CLI first (step 1)."
            return status()

        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        env = dict(os.environ, COLUMNS=str(COLUMNS), LINES=str(ROWS), TERM="xterm-256color")
        for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDECODE"):
            env.pop(k, None)
        try:
            argv = [cli, "auth", "login", "--claudeai"] if kind == "full" \
                else [cli, "setup-token"]
            proc = subprocess.Popen(argv, stdin=slave, stdout=slave,
                                    stderr=slave, env=env, start_new_session=True)
        except OSError as e:
            os.close(master)
            os.close(slave)
            _S.state, _S.message = "error", f"Couldn't start the Claude CLI: {e}"
            return status()
        os.close(slave)
        _S.state, _S.message = "running", "Finish the login in the browser window that opened."
        _S.auth_url, _S.prompt, _S.output = None, None, ""
        _S.proc, _S.master_fd, _S.started_at = proc, master, time.time()
        _S.kind, _S.cli = kind, cli
    threading.Thread(target=_reader, daemon=True, name="claude-login").start()
    return status()


def send(text: str) -> dict[str, Any]:
    """Type a line into the running login (e.g. a pasted code)."""
    with _S.lock:
        if _S.state == "running" and _S.master_fd is not None:
            os.write(_S.master_fd, text.strip().encode() + b"\r")
            _S.prompt = None
    return status()


def cancel() -> dict[str, Any]:
    with _S.lock:
        if _S.state == "running":
            _finish("idle", "Login cancelled.")
    return status()


def status() -> dict[str, Any]:
    return {
        "state": _S.state,
        "message": _S.message,
        "auth_url": _S.auth_url,
        "prompt": _S.prompt,
        "output": _S.output,
    }
