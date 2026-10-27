"""Claude via an Anthropic API key (the default auth mode).

Structured output uses a forced tool call: we hand Claude exactly one tool
whose input schema is the pydantic model, and ``tool_choice`` forces it, so
the reply is always a schema-shaped object. We still validate locally with
pydantic (bounds, lengths) before returning.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypeVar, cast

import anthropic
from pydantic import BaseModel, ValidationError

from .base import LLMError, tool_schema_for

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Errors worth retrying with backoff (rate limits, overload, flaky network).
_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


class AnthropicAPIClient:
    """``ClaudeClient`` backed by the ``anthropic`` SDK."""

    auth_mode = "api_key"

    def __init__(self, *, api_key: str, timeout: float = 90.0, max_attempts: int = 4) -> None:
        if not api_key:
            raise LLMError(
                "No ANTHROPIC_API_KEY set. Add one on the Setup page or in .env, "
                "or switch to Claude Code login mode."
            )
        # The SDK retries a little on its own; we add a longer backoff loop on
        # top for rate limits, which matter during a big scoring run.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._max_attempts = max_attempts

    # -- internals ---------------------------------------------------------

    def _create(self, **kwargs: Any) -> Any:
        delay = 2.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._client.messages.create(**kwargs)
            except _RETRYABLE as e:
                if attempt == self._max_attempts:
                    raise LLMError(f"Claude API unavailable after {attempt} attempts: {e}") from e
                log.warning("Claude API retry %d/%d in %.0fs: %s", attempt,
                            self._max_attempts, delay, e)
                time.sleep(delay)
                delay *= 2
            except anthropic.AuthenticationError as e:
                raise LLMError(
                    "Your Anthropic API key was rejected. Check it on the Setup page."
                ) from e
            except anthropic.APIError as e:
                raise LLMError(f"Claude API error: {e}") from e
        raise LLMError("unreachable")  # pragma: no cover

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
        tool = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": tool_schema_for(schema),
        }
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool_name},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._create(**kwargs)
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                try:
                    return schema.model_validate(cast(dict[str, Any], block.input))
                except ValidationError as e:
                    raise LLMError(f"Claude's answer did not match the schema: {e}") from e
        raise LLMError(f"Claude response contained no {tool_name} tool call")

    def text(
        self,
        prompt: str,
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2000,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._create(**kwargs)
        return "".join(b.text for b in response.content if b.type == "text").strip()
