"""Excel tracker writer (openpyxl).

Per spec Section 10:
- 12 columns, color-coded by status
- freeze_panes='A2', AutoFilter on the data range
- autosize column widths (capped so URLs don't blow up the layout)
- URL and Resume columns are hyperlinks when set

Color palette: green=applied, blue=resume-only, red=rejected, gray=dead,
yellow=pending/mismatch, light-green=new.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..models import Job, Status


class _Column(NamedTuple):
    """Header label + accessor that pulls the cell value from a Job."""

    header: str
    accessor: Callable[[Job], object]
    max_width: int = 30  # cap so JD-derived strings don't blow up layout


# Column order (matches the spec's tracker schema exactly).
COLUMNS: tuple[_Column, ...] = (
    _Column("Company", lambda j: j.company, max_width=22),
    _Column("Title", lambda j: j.title, max_width=45),
    _Column("Team", lambda j: j.team or "", max_width=25),
    _Column("Location", lambda j: j.location, max_width=28),
    _Column("Status", lambda j: j.status.value, max_width=18),
    _Column("Fit Score", lambda j: j.fit_score, max_width=12),
    _Column(
        "Verdict",
        lambda j: j.fit_verdict.value if j.fit_verdict else "",
        max_width=14,
    ),
    _Column("URL", lambda j: j.url, max_width=50),
    _Column("Resume", lambda j: j.resume_path or "", max_width=40),
    _Column("Applied At", lambda j: j.applied_at or "", max_width=20),
    _Column("Source", lambda j: j.source.value, max_width=16),
    _Column("Notes", lambda j: j.notes or "", max_width=40),
)


# Status → ARGB-less hex fill. openpyxl prepends 'FF' (alpha=opaque)
# automatically when given a 6-char hex.
STATUS_FILLS: dict[Status, str] = {
    Status.APPLIED: "C6EFCE",          # green
    Status.RESUME_ONLY: "BDD7EE",      # blue
    Status.REJECTED: "FFC7CE",         # red
    Status.AUTO_REJECTED: "FFC7CE",    # red
    Status.DEAD: "D9D9D9",             # gray
    Status.EXCLUDED: "D9D9D9",         # gray
    Status.PENDING: "FFEB9C",          # yellow
    Status.MISMATCH: "FFEB9C",         # yellow
    Status.NEW: "E2EFDA",              # light green
    Status.SKIPPED: "EAEAEA",          # light gray (spec didn't pin this; sensible default)
    Status.DISMISSED: "EAEAEA",        # light gray — user discarded (not interested)
    Status.ASSESSMENT_PASSED: "C6EFCE",  # green, same as applied
}


# Special columns we look up by header to apply hyperlinks.
URL_COL = 1 + next(i for i, c in enumerate(COLUMNS) if c.header == "URL")
RESUME_COL = 1 + next(i for i, c in enumerate(COLUMNS) if c.header == "Resume")


def write_tracker(jobs: list[Job], path: str | Path) -> Path:
    """Build `data/tracker.xlsx` from the current job list.

    Overwrites the existing file. Returns the resolved output path.
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    assert ws is not None  # active sheet always exists on a fresh Workbook
    ws.title = "Jobs"

    _write_header(ws)
    _write_data_rows(ws, jobs)
    _freeze_and_filter(ws, n_rows=len(jobs))
    _autosize(ws, n_rows=len(jobs))

    wb.save(out_path)
    return out_path


# --- internals -----------------------------------------------------------


def _write_header(ws: Worksheet) -> None:
    bold = Font(bold=True)
    header_fill = PatternFill(start_color="333333", end_color="333333", fill_type="solid")
    white = Font(bold=True, color="FFFFFF")
    for col_idx, col in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col.header)
        cell.font = white
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left")
    # `bold` referenced so an unused-var lint doesn't fire — kept around
    # for a future "secondary header row" if we ever add one.
    _ = bold


def _write_data_rows(ws: Worksheet, jobs: list[Job]) -> None:
    for row_idx, job in enumerate(jobs, start=2):
        fill_color = STATUS_FILLS.get(job.status)
        fill = (
            PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
            if fill_color
            else None
        )

        for col_idx, col in enumerate(COLUMNS, start=1):
            # Assign via .value (not the cell() kwarg) so mypy's tighter
            # cell() overload doesn't reject the wide accessor return type.
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = col.accessor(job)  # type: ignore[assignment]
            if fill is not None:
                cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=False)

        # Hyperlinks. openpyxl displays the cell value but the click
        # target is whatever we set on `.hyperlink`.
        url_cell = ws.cell(row=row_idx, column=URL_COL)
        if job.url:
            url_cell.hyperlink = job.url
            url_cell.font = Font(color="0563C1", underline="single")

        if job.resume_path:
            resume_cell = ws.cell(row=row_idx, column=RESUME_COL)
            resume_cell.hyperlink = job.resume_path
            resume_cell.font = Font(color="0563C1", underline="single")


def _freeze_and_filter(ws: Worksheet, *, n_rows: int) -> None:
    ws.freeze_panes = "A2"
    last_col_letter = get_column_letter(len(COLUMNS))
    # AutoFilter wants A1:<LastCol><LastRow>. With zero jobs we still
    # set it on the header row alone so filters are pre-armed.
    last_row = max(1, n_rows + 1)
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"


def _autosize(ws: Worksheet, *, n_rows: int) -> None:
    """Best-effort column width: longest visible string + 2, capped per column."""
    for col_idx, col in enumerate(COLUMNS, start=1):
        longest = len(col.header)
        for row in range(2, n_rows + 2):
            val = ws.cell(row=row, column=col_idx).value
            if val is None:
                continue
            longest = max(longest, len(str(val)))
        # +2 for breathing room, clamp to col.max_width to keep wide URLs
        # from making the sheet horribly horizontal.
        width = min(max(longest + 2, 10), col.max_width)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
