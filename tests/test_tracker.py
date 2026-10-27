"""Tests for the Excel tracker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from job_pipeline.models import Job, Source, Status, Verdict
from job_pipeline.tracker import COLUMNS, STATUS_FILLS, write_tracker


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, ML Platform",
        "team": "ML",
        "source": Source.GREENHOUSE,
        "url": "https://example.com/job/1",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "...",
    }
    defaults.update(overrides)
    return Job(**defaults)


# --- column shape -------------------------------------------------------


def test_tracker_has_twelve_columns() -> None:
    """Spec Section 10 mandates 12 columns."""
    assert len(COLUMNS) == 12


def test_tracker_column_order() -> None:
    """Order must match the spec exactly."""
    expected = [
        "Company", "Title", "Team", "Location", "Status", "Fit Score",
        "Verdict", "URL", "Resume", "Applied At", "Source", "Notes",
    ]
    assert [c.header for c in COLUMNS] == expected


# --- empty tracker ------------------------------------------------------


def test_write_empty_tracker(tmp_path: Path) -> None:
    """A zero-jobs tracker still has the header row and is valid Excel."""
    out = write_tracker([], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    # Headers in row 1.
    assert ws.cell(row=1, column=1).value == "Company"
    assert ws.cell(row=1, column=5).value == "Status"


def test_empty_tracker_has_freeze_pane(tmp_path: Path) -> None:
    out = write_tracker([], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    assert ws.freeze_panes == "A2"


# --- data rows ---------------------------------------------------------


def test_write_single_job(tmp_path: Path) -> None:
    job = _make_job(fit_score=8.5, fit_verdict=Verdict.APPLY, status=Status.PENDING)
    out = write_tracker([job], tmp_path / "t.xlsx")

    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    # Row 2 should be the job.
    assert ws.cell(row=2, column=1).value == "databricks"
    assert ws.cell(row=2, column=2).value == "Software Engineer, ML Platform"
    assert ws.cell(row=2, column=5).value == "pending"
    assert ws.cell(row=2, column=6).value == 8.5
    assert ws.cell(row=2, column=7).value == "apply"


def test_url_cell_is_hyperlink(tmp_path: Path) -> None:
    job = _make_job(url="https://example.com/job/abc")
    out = write_tracker([job], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    url_col = 1 + next(i for i, c in enumerate(COLUMNS) if c.header == "URL")
    cell = ws.cell(row=2, column=url_col)
    assert cell.hyperlink is not None
    assert cell.hyperlink.target == "https://example.com/job/abc"


def test_resume_cell_hyperlink_when_set(tmp_path: Path) -> None:
    job = _make_job(resume_path="/Users/a/resumes/foo.docx")
    out = write_tracker([job], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    resume_col = 1 + next(i for i, c in enumerate(COLUMNS) if c.header == "Resume")
    cell = ws.cell(row=2, column=resume_col)
    assert cell.hyperlink is not None


def test_resume_cell_no_hyperlink_when_unset(tmp_path: Path) -> None:
    job = _make_job(resume_path=None)
    out = write_tracker([job], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    resume_col = 1 + next(i for i, c in enumerate(COLUMNS) if c.header == "Resume")
    cell = ws.cell(row=2, column=resume_col)
    assert cell.hyperlink is None


# --- coloring ----------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_hex",
    [
        (Status.APPLIED, "C6EFCE"),
        (Status.RESUME_ONLY, "BDD7EE"),
        (Status.REJECTED, "FFC7CE"),
        (Status.AUTO_REJECTED, "FFC7CE"),
        (Status.DEAD, "D9D9D9"),
        (Status.EXCLUDED, "D9D9D9"),
        (Status.PENDING, "FFEB9C"),
        (Status.MISMATCH, "FFEB9C"),
        (Status.NEW, "E2EFDA"),
    ],
)
def test_status_fill_colors_match_spec(
    tmp_path: Path, status: Status, expected_hex: str
) -> None:
    """Spec Section 10 pins these hex codes — must not drift."""
    job = _make_job(status=status)
    out = write_tracker([job], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    fill = ws.cell(row=2, column=1).fill
    # openpyxl prepends FF (alpha=opaque) when saving 6-char hex.
    assert fill.fgColor.rgb.upper().endswith(expected_hex)


# --- freeze + filter ---------------------------------------------------


def test_freeze_panes_on_data(tmp_path: Path) -> None:
    out = write_tracker([_make_job()], tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    assert ws.freeze_panes == "A2"


def test_autofilter_covers_data_range(tmp_path: Path) -> None:
    jobs = [_make_job(job_id="1"), _make_job(job_id="2"), _make_job(job_id="3")]
    out = write_tracker(jobs, tmp_path / "t.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws is not None
    last_col = get_column_letter(len(COLUMNS))
    assert ws.auto_filter.ref == f"A1:{last_col}4"


def test_all_status_values_have_fills() -> None:
    """If a Status is added to the enum, STATUS_FILLS should cover it."""
    for status in Status:
        assert status in STATUS_FILLS, f"Missing fill for status {status}"
