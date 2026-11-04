"""Bounded public-web research, isolated from candidate data and local tools.

Both transports return provider-observed sources only — the later structured
synthesis may cite only these (or the saved posting), never invented links.

Sources come in two strengths, and the difference matters for honesty:

* ``kind="page"``    — text we actually have from the page: a fetched page's
                       content (Claude Code WebFetch) or the provider's verbatim
                       citation text (API web search). Has an ``excerpt``.
* ``kind="listing"`` — a search result link + title only. Proves the page
                       exists, NOT what it says. Never enough for a market
                       rating on its own (enforced in research/service.py).

Live-verified shapes (Claude Code 2.1.x via the Agent SDK, Nov 2026):
* WebSearch tool result content is a string "…Links: [{"title","url"}, …]…";
  the UserMessage's ``tool_use_result`` holds ``{"query", "results": [{"content":
  [{"title","url"}]} | str]}``.
* WebFetch tool result content is Claude's summary of the page; the URL is only
  in the tool *input*. ``tool_use_result`` holds ``{"code", "bytes", "result"}``.
  A failed fetch can come back with ``is_error`` unset (e.g. code 202, 0 bytes,
  "I don't have any content to analyze") — such results are dropped.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .base import ClaudeClient, LLMError

EXCERPT_CHARS = 2500
MAX_SOURCES = 60
MAX_SEARCHES = 6
MAX_FETCHES = 8
MAX_TURNS = 16


class WebSource(BaseModel):
    url: str
    title: str
    excerpt: str = ""
    kind: Literal["page", "listing"] = "listing"
    published: str = ""  # page age / date when the provider reports one


class WebEvidence(BaseModel):
    notes: str = ""
    sources: list[WebSource] = Field(default_factory=list)
    warning: str = ""


def safe_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        return p.scheme in {"https", "http"} and bool(p.hostname) and not p.username
    except ValueError:
        return False


class _Collector:
    """Merge sources by URL; a page with text always beats a bare listing."""

    def __init__(self) -> None:
        self._by_url: dict[str, WebSource] = {}

    def add(self, url: str, *, title: str = "", excerpt: str = "", published: str = "") -> None:
        url = url.strip().rstrip(".,;")
        if not safe_url(url):
            return
        excerpt = excerpt.strip()
        cur = self._by_url.get(url)
        if cur is None:
            if len(self._by_url) >= MAX_SOURCES:
                return
            self._by_url[url] = WebSource(
                url=url, title=title or url, excerpt=excerpt[:EXCERPT_CHARS],
                kind="page" if excerpt else "listing", published=published,
            )
            return
        if title and cur.title == cur.url:
            cur.title = title
        if published and not cur.published:
            cur.published = published
        if excerpt and excerpt not in cur.excerpt:
            joined = f"{cur.excerpt}\n…\n{excerpt}" if cur.excerpt else excerpt
            cur.excerpt = joined[:EXCERPT_CHARS]
            cur.kind = "page"

    def sources(self) -> list[WebSource]:
        # Pages first: they're what claims can rest on.
        return sorted(self._by_url.values(), key=lambda s: s.kind != "page")


# --- shared parsing helpers --------------------------------------------------

_LINKS_RE = re.compile(r"Links:\s*(\[.*?\])\s*(?:\n|$)", re.S)


def _search_links(content: Any, structured: Any) -> list[tuple[str, str]]:
    """(url, title) pairs from a WebSearch result, structured data first."""
    pairs: list[tuple[str, str]] = []
    if isinstance(structured, dict):
        for res in structured.get("results") or []:
            if isinstance(res, dict):
                for item in res.get("content") or []:
                    if isinstance(item, dict) and isinstance(item.get("url"), str):
                        pairs.append((item["url"], str(item.get("title") or "")))
    if pairs:
        return pairs
    text = content if isinstance(content, str) else json.dumps(content or "")
    for block in _LINKS_RE.findall(text):
        try:
            for item in json.loads(block):
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    pairs.append((item["url"], str(item.get("title") or "")))
        except ValueError:
            continue
    return pairs


_EMPTY_FETCH = re.compile(
    r"(don't|do not|cannot|can't) (have|see|access|find) (any )?(the )?(web page )?content|"
    r"page content .{0,40}(is|appears to be) empty|no content to analy[sz]e",
    re.I,
)


def _fetch_text(content: Any, structured: Any) -> tuple[str, str]:
    """(text, problem) from a WebFetch result; problem is non-empty if unusable."""
    text = content if isinstance(content, str) else ""
    if isinstance(content, list):
        text = "\n".join(str(i.get("text", "")) for i in content if isinstance(i, dict))
    if isinstance(structured, dict):
        code = structured.get("code")
        size = structured.get("bytes")
        if isinstance(code, int) and code >= 400:
            return "", f"HTTP {code}"
        if size == 0:
            return "", "empty page"
        text = str(structured.get("result") or text)
    if not text.strip() or _EMPTY_FETCH.search(text[:600]):
        return "", "no readable content"
    return text, ""


# --- API key transport (Anthropic server-side web search) ---------------------


def _api_research(client: Any, prompt: str, model: str) -> WebEvidence:
    response = client._create(
        model=model, max_tokens=5000,
        tools=[{"type": "web_search_20250305", "name": "web_search",
                "max_uses": MAX_SEARCHES}],
        messages=[{"role": "user", "content": prompt}],
    )
    blocks: list[dict[str, Any]] = response.model_dump()["content"]
    col = _Collector()
    errors = 0
    for b in blocks:
        if b.get("type") == "web_search_tool_result":
            content = b.get("content")
            if isinstance(content, dict) and content.get("type", "").endswith("_error"):
                errors += 1
                continue
            for item in content or []:
                if isinstance(item, dict) and item.get("url"):
                    # Result bodies are encrypted — a listing until a citation quotes it.
                    col.add(item["url"], title=str(item.get("title") or ""),
                            published=str(item.get("page_age") or ""))
        for cite in b.get("citations") or []:
            if isinstance(cite, dict) and cite.get("url"):
                col.add(cite["url"], title=str(cite.get("title") or ""),
                        excerpt=str(cite.get("cited_text") or ""))
    notes = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
    warnings = []
    if errors:
        warnings.append(f"{errors} search(es) failed.")
    if response.stop_reason in {"max_tokens", "pause_turn"}:
        warnings.append("Search stopped early; results are partial.")
    return WebEvidence(notes=notes, sources=col.sources(), warning=" ".join(warnings))


# --- Claude Code transport (Agent SDK, WebSearch + WebFetch only) ---------------


async def _cli_research(client: Any, prompt: str, model: str) -> WebEvidence:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )
    from claude_agent_sdk.types import HookContext, HookInput, HookJSONOutput

    from .cli_client import cli_model_alias

    counts: dict[str, int] = {}

    async def cap_tools(
        data: HookInput, tool_use_id: str | None, context: HookContext,
    ) -> HookJSONOutput:
        name = str(data.get("tool_name", ""))
        counts[name] = counts.get(name, 0) + 1
        limit = MAX_SEARCHES if name == "WebSearch" else MAX_FETCHES
        if counts[name] > limit:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "Research limit"}}
        return {}

    # tools= limits availability; allowed_tools alone would only pre-approve them.
    options = ClaudeAgentOptions(
        model=cli_model_alias(model), tools=["WebSearch", "WebFetch"],
        allowed_tools=["WebSearch", "WebFetch"], max_turns=MAX_TURNS,
        cli_path=client.cli_path, env=client._child_env(), setting_sources=[],
        cwd=tempfile.gettempdir(),
        hooks={"PreToolUse": [HookMatcher(matcher="WebSearch|WebFetch", hooks=[cap_tools])]},
    )
    col = _Collector()
    calls: dict[str, tuple[str, dict[str, Any]]] = {}   # tool_use_id → (name, input)
    unreadable: list[str] = []
    notes: list[str] = []
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    calls[b.id] = (b.name, b.input or {})
                elif isinstance(b, TextBlock):
                    notes.append(b.text)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            structured = getattr(msg, "tool_use_result", None)
            for block in msg.content:
                if not isinstance(block, ToolResultBlock) or block.is_error:
                    continue
                name, args = calls.get(block.tool_use_id, ("", {}))
                if name == "WebSearch":
                    for url, title in _search_links(block.content, structured):
                        col.add(url, title=title)
                elif name == "WebFetch":
                    url = str(args.get("url") or "")
                    text, problem = _fetch_text(block.content, structured)
                    if problem:
                        unreadable.append(f"{urlsplit(url).hostname or url} ({problem})")
                    else:
                        col.add(url, excerpt=text)
        elif isinstance(msg, ResultMessage):
            if msg.is_error:
                raise LLMError("Web research stopped before completion. Please retry.")
            if msg.result:
                notes.append(msg.result)
    warning = f"Couldn't read: {', '.join(unreadable[:5])}." if unreadable else ""
    return WebEvidence(notes="\n".join(notes)[-35000:], sources=col.sources(), warning=warning)


def web_research(client: ClaudeClient, prompt: str, *, model: str) -> WebEvidence:
    """Search public facts only; callers must never put resume/profile data here."""
    from .api_client import AnthropicAPIClient
    from .cli_client import _EXECUTOR, ClaudeCodeClient

    if isinstance(client, AnthropicAPIClient):
        return _api_research(client, prompt, model)
    if isinstance(client, ClaudeCodeClient):
        return _EXECUTOR.submit(asyncio.run, _cli_research(client, prompt, model)).result()
    raise LLMError("Web research is unavailable for this Claude client.")
