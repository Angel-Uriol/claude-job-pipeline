"""Excel tracker — a human-friendly view of jobs.json.

`write_tracker` is the single public entry. It rebuilds the workbook
from scratch on every run; there's no incremental update because
overwriting is simpler and openpyxl is fast enough for thousands of rows.
"""

from .xlsx_writer import COLUMNS, STATUS_FILLS, write_tracker

__all__ = ["COLUMNS", "STATUS_FILLS", "write_tracker"]
