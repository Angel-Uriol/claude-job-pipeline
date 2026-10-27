"""Tests for the `daily` super-command.

Exercises only the chaining: each underlying subcommand is mocked so
we don't need API keys or real LLM calls — we just verify the order
and the short-circuit behavior on failure.
"""

from __future__ import annotations

import argparse

import pytest

from job_pipeline import cli


def _args() -> argparse.Namespace:
    return argparse.Namespace(verbose=False, config="config.yaml")


def test_daily_runs_each_subcommand_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five steps fire, in scrape → pre-filter → deep-fit → digest → tracker order."""
    order: list[str] = []

    def make_stub(name: str):
        def stub(args: argparse.Namespace) -> int:
            order.append(name)
            return 0

        return stub

    monkeypatch.setattr(cli, "cmd_scrape", make_stub("scrape"))
    monkeypatch.setattr(cli, "cmd_pre_filter", make_stub("pre-filter"))
    monkeypatch.setattr(cli, "cmd_deep_fit", make_stub("deep-fit"))
    monkeypatch.setattr(cli, "cmd_digest", make_stub("digest"))
    monkeypatch.setattr(cli, "cmd_tracker", make_stub("tracker"))

    rc = cli.cmd_daily(_args())
    assert rc == 0
    assert order == ["scrape", "pre-filter", "deep-fit", "digest", "tracker"]


def test_daily_short_circuits_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If pre-filter exits non-zero, deep-fit/digest/tracker must NOT run."""
    order: list[str] = []

    monkeypatch.setattr(cli, "cmd_scrape", lambda a: order.append("scrape") or 0)
    monkeypatch.setattr(cli, "cmd_pre_filter", lambda a: order.append("pre-filter") or 2)
    monkeypatch.setattr(cli, "cmd_deep_fit", lambda a: order.append("deep-fit") or 0)
    monkeypatch.setattr(cli, "cmd_digest", lambda a: order.append("digest") or 0)
    monkeypatch.setattr(cli, "cmd_tracker", lambda a: order.append("tracker") or 0)

    rc = cli.cmd_daily(_args())
    assert rc == 2
    assert order == ["scrape", "pre-filter"]


def test_daily_passes_force_false_to_subcommands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daily never re-scores — sub_args.force must be False."""
    captured: dict[str, bool] = {}

    def capture_force(name: str):
        def stub(args: argparse.Namespace) -> int:
            captured[name] = getattr(args, "force", None)
            return 0

        return stub

    monkeypatch.setattr(cli, "cmd_scrape", capture_force("scrape"))
    monkeypatch.setattr(cli, "cmd_pre_filter", capture_force("pre-filter"))
    monkeypatch.setattr(cli, "cmd_deep_fit", capture_force("deep-fit"))
    monkeypatch.setattr(cli, "cmd_digest", capture_force("digest"))
    monkeypatch.setattr(cli, "cmd_tracker", capture_force("tracker"))

    cli.cmd_daily(_args())
    assert captured["pre-filter"] is False
    assert captured["deep-fit"] is False
