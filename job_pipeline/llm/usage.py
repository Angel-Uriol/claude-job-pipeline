"""Usage ledger: one JSON line per Claude call, in data/usage.jsonl.

Lets the app show "Claude this month: $3.20" and enforce the monthly budget.
It's machine-level (all profiles) and never leaves this computer. Each line:
  {ts, profile, feature, model, source, cost_usd, input_tokens,
   output_tokens, web_searches}
`source` is "api" (cost estimated from token counts × llm/prices.py) or
"claude_code" (the CLI's API-equivalent cost — counts against your plan).

Tag what a call was for with `with usage_feature("cover-letter"): ...`.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from .. import paths

log = logging.getLogger(__name__)

_feature: contextvars.ContextVar[str] = contextvars.ContextVar("usage_feature", default="other")
_lock = threading.Lock()


def ledger_path() -> Path:
    override = os.environ.get("JOB_PIPELINE_USAGE_FILE")
    return Path(override) if override else paths.REPO_ROOT / "data" / "usage.jsonl"


@contextmanager
def usage_feature(name: str) -> Iterator[None]:
    token = _feature.set(name)
    try:
        yield
    finally:
        _feature.reset(token)


def current_feature() -> str:
    return _feature.get()


def record(*, model: str, cost_usd: float, source: str, input_tokens: int = 0,
           output_tokens: int = 0, web_searches: int = 0, feature: str | None = None) -> None:
    """Append one call. Never raises — metering must not break the pipeline."""
    try:
        profile = paths.active_slug() or ""
    except Exception:  # noqa: BLE001
        profile = ""
    row = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": profile, "feature": feature or current_feature(),
        "model": model, "source": source, "cost_usd": round(float(cost_usd or 0), 6),
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "web_searches": int(web_searches),
    }
    try:
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log.warning("Couldn't write the usage ledger: %s", e)


def record_api_response(model: str, response: Any) -> None:
    """From an Anthropic SDK Message (its `usage` block)."""
    from .prices import cost_usd

    u = getattr(response, "usage", None)
    if u is None:
        return
    inp = int(getattr(u, "input_tokens", 0) or 0)
    out = int(getattr(u, "output_tokens", 0) or 0)
    cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    stu = getattr(u, "server_tool_use", None)
    searches = int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0
    record(model=model, source="api", input_tokens=inp + cr + cw, output_tokens=out,
           web_searches=searches,
           cost_usd=cost_usd(model, inp, out, cache_read_tokens=cr, cache_write_tokens=cw,
                             web_searches=searches))


def record_cli_result(model: str, message: Any) -> None:
    """From a Claude Agent SDK ResultMessage (`total_cost_usd`, `usage`)."""
    cost = getattr(message, "total_cost_usd", None)
    usage = getattr(message, "usage", None) or {}
    get = usage.get if isinstance(usage, dict) else (lambda k, d=0: getattr(usage, k, d))
    record(model=model, source="claude_code", cost_usd=float(cost or 0),
           input_tokens=int(get("input_tokens", 0) or 0),
           output_tokens=int(get("output_tokens", 0) or 0))


def rows(month: str | None = None) -> list[dict[str, Any]]:
    """Ledger rows, optionally only for one month ("2026-11")."""
    p = ledger_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if month is None or str(row.get("ts", "")).startswith(month):
            out.append(row)
    return out


def this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def month_spend(month: str | None = None) -> float:
    return round(sum(float(r.get("cost_usd") or 0) for r in rows(month or this_month())), 4)


def by_feature(month: str | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows(month or this_month()):
        f = str(r.get("feature") or "other")
        out[f] = round(out.get(f, 0.0) + float(r.get("cost_usd") or 0), 4)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


P = ParamSpec("P")
R = TypeVar("R")


def tagged(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator form of `usage_feature` for whole functions."""
    def wrap(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with usage_feature(name):
                return fn(*args, **kwargs)
        return inner
    return wrap
