"""Jobi's chat loop, for both ways of connecting Claude.

* API key: Anthropic SDK, streamed, with a manual tool loop so every tool
  call shows a progress line and change cards appear as soon as they are
  made. Adaptive thinking.
* Claude Code login: the Claude Agent SDK with an in-process MCP server
  that wraps the same tools. Every built-in tool (shell, files, web) is
  denied, so Jobi can only use the tools in tools.py.

`run_turn` is synchronous and reports through `emit(event)`:
  {"type": "status", "text"}    a plain progress line ("Checking Chrome…")
  {"type": "text", "text"}      streamed reply text
  {"type": "proposal", "card"}  a change card (proposals.Proposal)
  {"type": "handoff", "text"}   a ready-to-paste prompt for Claude Code
  {"type": "done", "cost_usd", "text"}
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .. import paths
from ..config import Secrets, load_config, load_secrets
from ..llm.base import LLMError, tool_schema_for
from ..llm.budget import check as budget_check
from ..llm.factory import _refuse_in_demo
from ..llm.usage import tagged
from . import proposals, tools

Emit = Callable[[dict[str, Any]], None]

DEFAULT_MODEL = "claude-opus-5-5"
MAX_STEPS = 12          # tool round-trips per user message
HISTORY_TURNS = 20      # earlier messages sent back to Claude
MAX_TOKENS = 8000
_PROMPT = paths.REPO_ROOT / "prompts" / "jobi_system.txt"


# --- chat history (per profile, inside the gitignored profiles/ folder) -----------


def _history_file() -> Path:
    return paths.profile_dir() / "data" / "jobi_chat.json"


def load_history() -> list[dict[str, Any]]:
    f = _history_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def save_history(items: list[dict[str, Any]]) -> None:
    f = _history_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items[-200:], indent=2), encoding="utf-8")


def append_history(role: str, text: str, **extra: Any) -> None:
    items = load_history()
    items.append({"role": role, "text": text, "ts": datetime.now(UTC).isoformat(), **extra})
    save_history(items)


def reset_history() -> None:
    save_history([])
    proposals.clear()


def _model() -> str:
    try:
        return load_config().llm.assistant_model or DEFAULT_MODEL
    except Exception:  # noqa: BLE001 - a broken config shouldn't stop Jobi helping fix it
        return DEFAULT_MODEL


def model_name() -> str:
    return _model()


def system_prompt(page: str) -> str:
    return (_PROMPT.read_text(encoding="utf-8")
            .replace("{page}", page or "/").replace("{today}", date.today().isoformat()))


def _past_messages() -> list[dict[str, Any]]:
    """Earlier turns as plain text, merged so roles alternate (the API needs it)."""
    out: list[dict[str, Any]] = []
    for m in load_history()[-HISTORY_TURNS:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


# --- the turn -----------------------------------------------------------------------------


@tagged("jobi")
def run_turn(message: str, *, page: str, ctx: tools.ToolContext, emit: Emit,
             secrets: Secrets | None = None) -> dict[str, Any]:
    """Answer one user message (with tool calls). Saves both sides to history."""
    s = secrets or load_secrets()
    _refuse_in_demo()
    budget_check(s)
    model = _model()
    history = _past_messages()
    append_history("user", message)

    cards: list[str] = []
    handoffs: list[str] = []

    def on_proposal(p: proposals.Proposal) -> None:
        cards.append(p.id)
        emit({"type": "proposal", "card": p.model_dump()})

    ctx.on_proposal = on_proposal
    ctx.on_status = lambda text: emit({"type": "status", "text": text})
    ctx.page = page

    def tool_runner(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        result, is_error = tools.call(name, args, ctx)
        if name == "handoff_prompt" and not is_error:
            text = json.loads(result).get("handoff", "")
            handoffs.append(text)
            emit({"type": "handoff", "text": text})
        return result, is_error

    system = system_prompt(page)
    if s.auth_mode == "claude_code":
        text, cost = _run_cli(message, history, system, model, s, tool_runner, emit)
    else:
        text, cost = _run_api(message, history, system, model, s, tool_runner, emit)
    append_history("assistant", text, cards=cards, handoff=handoffs[-1] if handoffs else "",
                   cost_usd=round(cost, 4))
    emit({"type": "done", "cost_usd": round(cost, 4), "text": text})
    return {"text": text, "cost_usd": cost, "cards": cards}


ToolRunner = Callable[[str, dict[str, Any]], tuple[str, bool]]


def _api_tools() -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description,
             "input_schema": tool_schema_for(t.schema), "eager_input_streaming": True}
            for t in tools.TOOLS]


def _run_api(message: str, history: list[dict[str, Any]], system: str, model: str,
             s: Secrets, run_tool: ToolRunner, emit: Emit) -> tuple[str, float]:
    import anthropic

    from ..llm.prices import cost_usd
    from ..llm.usage import record_api_response

    if not s.anthropic_api_key:
        raise LLMError("No Anthropic API key saved — add one on the Setup page.")
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": message}]
    texts: list[str] = []
    total = 0.0
    retries = 0
    for _ in range(MAX_STEPS):
        try:
            with client.messages.stream(
                model=model, max_tokens=MAX_TOKENS, system=system,
                tools=_api_tools(),  # type: ignore[arg-type]
                thinking={"type": "adaptive"},
                messages=messages,  # type: ignore[arg-type]
            ) as stream:
                parts: list[str] = []
                for event in stream:
                    if event.type == "text":
                        parts.append(event.text)
                        emit({"type": "text", "text": event.text})
                response = stream.get_final_message()
            retries = 0
        except ValueError:
            # A tool input the SDK couldn't parse at all: re-issue the turn (bounded).
            retries += 1
            if retries > 2:
                raise LLMError("Claude sent a garbled tool call. Please try again.") from None
            continue
        except anthropic.APIError as e:
            raise LLMError(f"Claude API error: {e}") from e
        record_api_response(model, response)
        u = response.usage
        total += cost_usd(model, int(u.input_tokens or 0), int(u.output_tokens or 0),
                          cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
                          cache_write_tokens=int(
                              getattr(u, "cache_creation_input_tokens", 0) or 0))
        if parts:
            texts.append("".join(parts))
            emit({"type": "text", "text": "\n\n"})
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "refusal" or not uses:
            break
        if response.stop_reason == "max_tokens":
            texts.append("(My answer got cut off — please ask again more specifically.)")
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for b in uses:
            args = b.input if isinstance(b.input, dict) else {}
            out, is_error = run_tool(b.name, args)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out,
                            "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        texts.append("(I stopped after several steps — tell me how you'd like to continue.)")
    return "\n\n".join(t.strip() for t in texts if t.strip()), total


def _run_cli(message: str, history: list[dict[str, Any]], system: str, model: str,
             s: Secrets, run_tool: ToolRunner, emit: Emit) -> tuple[str, float]:
    from ..chrome_runner import _child_env, _require_cli
    from ..llm.cli_client import cli_model_alias
    from ..llm.usage import record_cli_result

    try:
        cli = _require_cli(s)
    except RuntimeError as e:
        raise LLMError(str(e)) from e

    async def go() -> tuple[str, float]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            create_sdk_mcp_server,
            query,
            tool,
        )

        def wrap(t: tools.Tool) -> Any:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                out, is_error = await asyncio.to_thread(run_tool, t.name, args)
                return {"content": [{"type": "text", "text": out}], "is_error": is_error}

            return tool(t.name, t.description, tool_schema_for(t.schema))(handler)

        server = create_sdk_mcp_server("jobi", tools=[wrap(t) for t in tools.TOOLS])
        transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        prompt = (f"Earlier in this chat:\n{transcript}\n\nUSER: {message}" if transcript
                  else message)
        options = ClaudeAgentOptions(
            model=cli_model_alias(model),
            system_prompt=system,
            mcp_servers={"jobi": server},
            allowed_tools=[f"mcp__jobi__{t.name}" for t in tools.TOOLS],
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit", "WebFetch",
                              "WebSearch", "Read", "Glob", "Grep", "Task", "Agent"],
            max_turns=MAX_STEPS * 2,
            cli_path=cli,
            env=_child_env(cli, s),
            setting_sources=[],
            cwd=tempfile.gettempdir(),
        )
        texts: list[str] = []
        cost = 0.0
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        texts.append(b.text)
                        emit({"type": "text", "text": b.text + "\n\n"})
            elif isinstance(msg, ResultMessage):
                cost = float(getattr(msg, "total_cost_usd", 0) or 0)
                record_cli_result(model, msg)
        return "\n\n".join(t.strip() for t in texts), cost

    try:
        return asyncio.run(go())
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 - CLI/SDK failures → readable message
        raise LLMError(f"Couldn't run Jobi through Claude Code: {e}") from e
