"""Tests for the persistent seen-ID ledger.

The ledger is the durable backstop that remembers every dedup-key the
pipeline has ever stored, so a fresh discovery run never resurfaces a
posting the user already reviewed — even if that row was later deleted
from jobs.json.
"""

from __future__ import annotations

from pathlib import Path

from job_pipeline.seen_ledger import SeenLedger, ledger_path_for


def test_ledger_path_is_sibling_of_jobs_json() -> None:
    p = ledger_path_for("/some/where/data/jobs.json")
    assert p == Path("/some/where/data/seen_jobs.json")


def test_add_contains_and_len(tmp_path: Path) -> None:
    led = SeenLedger(tmp_path / "seen.json")
    led.load()
    assert len(led) == 0
    assert not led.contains(("databricks", "123"))

    led.add(("databricks", "123"))
    led.add(("stripe", "se|payments"))
    assert led.contains(("databricks", "123"))
    assert led.contains(("stripe", "se|payments"))
    assert not led.contains(("databricks", "999"))
    assert len(led) == 2


def test_add_is_idempotent(tmp_path: Path) -> None:
    led = SeenLedger(tmp_path / "seen.json")
    led.load()
    led.add(("a", "1"))
    led.add(("a", "1"))
    assert len(led) == 1


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    led = SeenLedger(path)
    led.load()
    led.add(("databricks", "123"))
    led.add(("stripe", "se|payments"))
    led.save()

    # A fresh instance reading the same file sees the same keys.
    reloaded = SeenLedger(path)
    reloaded.load()
    assert reloaded.contains(("databricks", "123"))
    assert reloaded.contains(("stripe", "se|payments"))
    assert len(reloaded) == 2


def test_load_missing_file_starts_empty(tmp_path: Path) -> None:
    led = SeenLedger(tmp_path / "does-not-exist.json")
    led.load()  # must not raise
    assert len(led) == 0


def test_load_corrupt_file_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    led = SeenLedger(path)
    led.load()  # must not raise
    assert len(led) == 0


def test_load_accepts_bare_list_payload(tmp_path: Path) -> None:
    """Tolerate an older/alternate on-disk shape (a bare JSON list)."""
    path = tmp_path / "seen.json"
    path.write_text('["a\\u001f1", "b\\u001f2"]', encoding="utf-8")
    led = SeenLedger(path)
    led.load()
    assert led.contains(("a", "1"))
    assert led.contains(("b", "2"))


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "seen.json"
    led = SeenLedger(path)
    led.load()
    led.add(("a", "1"))
    led.save()
    assert path.exists()


def test_save_is_atomic_no_temp_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    led = SeenLedger(path)
    led.load()
    led.add(("a", "1"))
    led.save()
    # No leftover .seen-*.json.tmp files in the directory.
    leftovers = list(tmp_path.glob(".seen-*.json.tmp"))
    assert leftovers == []
