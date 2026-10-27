"""The one interface every Claude call in the pipeline goes through.

Two concrete clients implement it:

* ``AnthropicAPIClient`` (api_client.py) — the default. Uses an Anthropic API
  key from ``.env``. Structured output is enforced with a forced tool call.
* ``ClaudeCodeClient`` (cli_client.py) — opt-in. Drives the user's own,
  locally logged-in Claude Code CLI through the Claude Agent SDK. Structured
  output is requested as JSON and validated (with one corrective retry).

Feature modules (pre-filter, deep-fit, resume tailor, questionnaire, ...)
only ever see ``ClaudeClient``, so swapping auth modes never touches them —
and tests inject a fake that returns canned pydantic objects.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """A Claude call could not produce a usable answer (auth, network, or an
    answer that never validated). Message is safe to show in the UI."""


class ClaudeClient(Protocol):
    """Minimal surface the pipeline needs from Claude."""

    #: "api_key" or "claude_code" — shown in the UI / doctor output.
    auth_mode: str

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
        """Return an instance of ``schema`` produced by Claude."""
        ...

    def text(
        self,
        prompt: str,
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2000,
    ) -> str:
        """Return a plain-text answer."""
        ...


# --- helpers shared by both clients ---------------------------------------


def tool_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> a self-contained JSON Schema for a tool's input.

    Pydantic emits ``$defs`` + ``$ref`` for nested models. We inline them so
    the schema is one flat tree (simplest for both the API and for pasting
    into a prompt), and drop the ``title`` noise.
    """
    raw = schema.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(copy.deepcopy(defs[name]))
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    resolved = resolve(raw)
    assert isinstance(resolved, dict)
    return resolved


def extract_json(text: str) -> Any:
    """Pull one JSON object out of a reply that may be fenced or chatty."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in the model response")
    return json.loads(t[start : end + 1])
