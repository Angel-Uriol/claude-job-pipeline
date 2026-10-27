"""Tests for the Claude client layer (no network: the SDK is mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from job_pipeline.config import Secrets
from job_pipeline.llm import LLMError, build_llm, llm_available
from job_pipeline.llm.api_client import AnthropicAPIClient
from job_pipeline.llm.base import extract_json, tool_schema_for


class _Inner(BaseModel):
    x: int


class _Outer(BaseModel):
    name: str
    inner: _Inner
    items: list[_Inner] = []


def test_tool_schema_inlines_refs() -> None:
    schema = tool_schema_for(_Outer)
    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    assert schema["properties"]["inner"]["properties"]["x"]["type"] == "integer"
    assert schema["properties"]["items"]["items"]["properties"]["x"]["type"] == "integer"


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here you go: {"a": 1} hope that helps'],
)
def test_extract_json_handles_fences_and_chatter(raw: str) -> None:
    assert extract_json(raw) == {"a": 1}


def test_extract_json_raises_without_object() -> None:
    with pytest.raises(ValueError):
        extract_json("no json here")


# --- API-key client ---------------------------------------------------------


def _client_with_response(content: list[Any]) -> AnthropicAPIClient:
    c = AnthropicAPIClient(api_key="sk-ant-test")
    c._client = MagicMock()
    c._client.messages.create.return_value = SimpleNamespace(content=content)
    return c


def test_api_client_requires_key() -> None:
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicAPIClient(api_key="")


def test_api_client_structured_forces_tool_and_validates() -> None:
    block = SimpleNamespace(type="tool_use", name="submit", input={"name": "a", "inner": {"x": 3}})
    c = _client_with_response([block])
    out = c.structured("prompt", _Outer, model="claude-haiku-5-5", system="sys")
    assert out.inner.x == 3
    kwargs = c._client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit"}
    assert kwargs["system"] == "sys"
    assert kwargs["tools"][0]["input_schema"]["properties"]["name"]["type"] == "string"


def test_api_client_structured_bad_shape_raises_llm_error() -> None:
    block = SimpleNamespace(type="tool_use", name="submit", input={"name": "a"})
    c = _client_with_response([block])
    with pytest.raises(LLMError, match="schema"):
        c.structured("prompt", _Outer, model="m")


def test_api_client_text_joins_text_blocks() -> None:
    c = _client_with_response([SimpleNamespace(type="text", text="Hello "),
                               SimpleNamespace(type="text", text="world")])
    assert c.text("hi", model="m") == "Hello world"


# --- factory ------------------------------------------------------------------


def test_build_llm_api_mode_without_key_raises() -> None:
    with pytest.raises(LLMError):
        build_llm(Secrets(JOB_PIPELINE_AUTH_MODE="api_key", ANTHROPIC_API_KEY=""))


def test_build_llm_api_mode_with_key() -> None:
    client = build_llm(Secrets(JOB_PIPELINE_AUTH_MODE="api_key", ANTHROPIC_API_KEY="sk-ant-x"))
    assert client.auth_mode == "api_key"


def test_llm_available_claude_code_depends_on_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import job_pipeline.llm.cli_client as cc

    s = Secrets(JOB_PIPELINE_AUTH_MODE="claude_code")
    monkeypatch.setattr(cc, "resolve_claude_cli", lambda _p="": None)
    assert llm_available(s) is False
    monkeypatch.setattr(cc, "resolve_claude_cli", lambda _p="": "/usr/local/bin/claude")
    assert llm_available(s) is True


def test_claude_code_client_retries_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import job_pipeline.llm.cli_client as cc

    monkeypatch.setattr(cc, "resolve_claude_cli", lambda _p="": "/bin/echo")
    monkeypatch.setattr(cc, "scrub_process_env", lambda: False)
    client = cc.ClaudeCodeClient()
    replies = iter(["not json", '{"x": 7}'])
    prompts: list[str] = []

    def fake_run(prompt: str, model: str, system: str | None) -> str:
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(client, "_run", fake_run)
    assert client.structured("p", _Inner, model="m").x == 7
    assert "INVALID" in prompts[1]


def test_scrub_process_env_removes_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from job_pipeline.llm.cli_client import scrub_process_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-go")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert scrub_process_env() is True
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "CLAUDECODE" not in os.environ
