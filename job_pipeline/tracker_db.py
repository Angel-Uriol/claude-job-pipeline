"""The built-in application tracker (SQLite at `tracker.db_path`).

The **Track** tab reads + mutates this DB. It is the system of record for
"where am I with each application": status, notes, stars, applied dates, and
the tailored resume that went out. Jobs arrive here from Discover via
`handoff.run_handoff` (or the Chrome Claude text importer), which go through
`insert_jobs` so dedup + the per-company cap always apply.

stdlib `sqlite3` only; connections are short-lived per call (single user).
The schema is created on first use and migrated additively.

Resume *generation* is intentionally NOT here — resumes are produced in the
Discover tab and carried into the tracker by the handoff.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config

# The 12 application states the tracker recognizes.
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "new",
        "to_apply",
        "applied",
        "recruiter_screen",
        "phone_screen",
        "take_home",
        "onsite",
        "offer",
        "rejected",
        "auto_rejected",
        "withdrawn",
        "skip",
    }
)


SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id                TEXT PRIMARY KEY,
        batch             INTEGER,
        company           TEXT NOT NULL,
        role              TEXT NOT NULL,
        role_tier         TEXT,
        role_category     TEXT,
        job_id            TEXT,
        location          TEXT,
        location_pass     TEXT,
        posted            TEXT,
        level             TEXT,
        salary            TEXT,
        h1b               TEXT,   -- visa-sponsorship stance from the JD
        url               TEXT,
        fit_score         REAL,
        stack_score       REAL,
        level_score       REAL,
        function_score    REAL,
        bridgeability     REAL,
        desirability      REAL,
        verdict           TEXT DEFAULT 'CONSIDER',
        bridge_notes      TEXT,
        key_requirements  TEXT,   -- JSON array as text
        key_nice_to_haves TEXT,   -- JSON array as text
        full_jd_text      TEXT,
        resume_number     INTEGER,
        resume_variant    TEXT,
        starred           INTEGER DEFAULT 0,
        hidden            INTEGER DEFAULT 0,  -- soft-hide: out of view, kept for dedup
        status            TEXT DEFAULT 'new',
        notes             TEXT,
        applied_at        TEXT,
        added_at          TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contacts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        company       TEXT,
        name          TEXT,
        title         TEXT,
        linkedin_url  TEXT,
        opening_line  TEXT,
        batch         INTEGER,
        outreached    INTEGER DEFAULT 0,
        added_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batches (
        batch_number  INTEGER PRIMARY KEY,
        companies     TEXT,
        roles_added   INTEGER,
        skipped       INTEGER,
        added_at      TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resumes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id        TEXT REFERENCES jobs(id),
        resume_number INTEGER,
        variant       TEXT,
        file_path     TEXT,
        generated_at  TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_at  TEXT DEFAULT (datetime('now'))
    )
    """,
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_jobs_company_role ON jobs(company, role)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_verdict      ON jobs(verdict)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status       ON jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_fit_score    ON jobs(fit_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_hidden       ON jobs(hidden)",
    "CREATE INDEX IF NOT EXISTS idx_resumes_job       ON resumes(job_id)",
]

# Additive column migrations for DBs created before a column existed:
# (table, column, ALTER statement). Run before the indexes.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("jobs", "hidden", "ALTER TABLE jobs ADD COLUMN hidden INTEGER DEFAULT 0"),
]

# Columns `insert_jobs` writes (id + batch are filled in by the importer).
INSERT_COLUMNS = (
    "id batch company role role_tier role_category job_id location location_pass "
    "posted level salary h1b url fit_score stack_score level_score function_score "
    "bridgeability desirability verdict bridge_notes key_requirements "
    "key_nice_to_haves full_jd_text starred status notes applied_at"
).split()


def db_path(cfg: Config) -> Path:
    """Path to the tracker's SQLite DB (relative paths resolve from cwd)."""
    return Path(cfg.tracker.db_path).expanduser()


def resume_output_dir(cfg: Config) -> Path:
    """Where generated resumes live (download guard for the Track tab)."""
    return Path(cfg.paths.resumes_dir).expanduser()


def db_exists(cfg: Config) -> bool:
    return db_path(cfg).exists()


def init_db(cfg: Config) -> Path:
    """Create the schema if missing and apply additive migrations. Idempotent."""
    p = db_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        cur = conn.cursor()
        for stmt in SCHEMA:
            cur.execute(stmt)
        for table, column, alter_sql in MIGRATIONS:
            cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                cur.execute(alter_sql)
        for stmt in INDEXES:
            cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return p


def _open(cfg: Config) -> sqlite3.Connection:
    init_db(cfg)
    conn = sqlite3.connect(db_path(cfg))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _serialize(row: sqlite3.Row, resume_path: str | None) -> dict[str, Any]:
    """Shape a row for the UI: parse the JSON list columns and nest the
    5 axes under `fit_breakdown`."""
    d: dict[str, Any] = dict(row)
    for k in ("key_requirements", "key_nice_to_haves"):
        raw = d.get(k)
        if raw:
            try:
                d[k] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        else:
            d[k] = []
    d["fit_breakdown"] = {
        "stack": d.pop("stack_score", None),
        "level": d.pop("level_score", None),
        "function": d.pop("function_score", None),
        "bridgeability": d.pop("bridgeability", None),
        "desirability": d.pop("desirability", None),
    }
    d["resume_file_path"] = resume_path
    return d


def list_jobs(
    cfg: Config,
    *,
    search: str = "",
    location: str = "all",
    verdict: str = "all",
    status: str = "all",
    company: str = "all",
    sort: str = "score",
    hidden: str = "exclude",
) -> list[dict[str, Any]]:
    """Filtered + sorted tracker jobs, each with its latest resume path.

    `hidden`: 'exclude' (default), 'only', or 'all'.
    `company`: exact company name to filter to (case-insensitive), or 'all'.
    """
    if not db_exists(cfg):
        return []

    where: list[str] = []
    params: list[Any] = []
    if hidden == "only":
        where.append("IFNULL(hidden, 0) = 1")
    elif hidden != "all":
        where.append("IFNULL(hidden, 0) = 0")
    if verdict != "all":
        where.append("verdict = ?")
        params.append(verdict)
    if status != "all":
        where.append("status = ?")
        params.append(status)
    if company != "all":
        where.append("LOWER(company) = LOWER(?)")
        params.append(company)
    if location in ("preferred", "ca"):
        where.append("LOWER(IFNULL(location_pass, '')) LIKE 'yes (preferred)%'")
    elif location == "remote":
        where.append("LOWER(IFNULL(location_pass, '')) LIKE 'yes (remote)%'")

    if sort == "applied_at":
        order = "applied_at DESC, fit_score DESC"
    elif sort == "company":
        order = "company COLLATE NOCASE ASC, fit_score DESC"
    else:
        order = "starred DESC, fit_score DESC"

    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order}"

    conn = _open(cfg)
    try:
        resume_paths = {
            r["job_id"]: r["file_path"]
            for r in conn.execute(
                "SELECT job_id, file_path FROM resumes "
                "WHERE id IN (SELECT MAX(id) FROM resumes GROUP BY job_id)"
            )
        }
        rows = [
            _serialize(r, resume_paths.get(r["id"]))
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()

    # Free-text search in Python (small dataset).
    if search:
        needle = search.lower()
        fields = ("company", "role", "location", "bridge_notes", "full_jd_text", "notes")

        def _matches(j: dict[str, Any]) -> bool:
            hay = " ".join(str(j.get(k) or "") for k in fields).lower()
            return needle in hay

        rows = [r for r in rows if _matches(r)]

    return rows


def stats(cfg: Config) -> dict[str, Any]:
    """Visible-tracker counts (hidden excluded), plus hidden_count."""
    if not db_exists(cfg):
        return {
            "total": 0,
            "by_status": {},
            "by_verdict": {},
            "by_company": {},
            "applied_count": 0,
            "hidden_count": 0,
        }
    vis = "WHERE IFNULL(hidden, 0) = 0"
    conn = _open(cfg)
    try:
        total = int(conn.execute(f"SELECT COUNT(*) FROM jobs {vis}").fetchone()[0])
        by_status = {
            (r["status"] or "(none)"): r["n"]
            for r in conn.execute(f"SELECT status, COUNT(*) AS n FROM jobs {vis} GROUP BY status")
        }
        by_verdict = {
            (r["verdict"] or "(none)"): r["n"]
            for r in conn.execute(f"SELECT verdict, COUNT(*) AS n FROM jobs {vis} GROUP BY verdict")
        }
        by_company = {
            r["company"]: r["n"]
            for r in conn.execute(
                f"SELECT company, COUNT(*) AS n FROM jobs {vis} "
                "GROUP BY company ORDER BY n DESC, company COLLATE NOCASE ASC"
            )
        }
        applied = int(
            conn.execute(f"SELECT COUNT(*) FROM jobs {vis} AND status = 'applied'").fetchone()[0]
        )
        hidden_count = int(
            conn.execute("SELECT COUNT(*) FROM jobs WHERE IFNULL(hidden, 0) = 1").fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "total": total,
        "by_status": by_status,
        "by_verdict": by_verdict,
        "by_company": by_company,
        "applied_count": applied,
        "hidden_count": hidden_count,
    }


def set_status(cfg: Config, job_id: str, status: str) -> bool:
    """Update a job's status; auto-stamp applied_at on first →applied."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    conn = _open(cfg)
    try:
        applied = (
            ", applied_at = COALESCE(applied_at, datetime('now'))"
            if status == "applied"
            else ""
        )
        cur = conn.execute(
            f"UPDATE jobs SET status = ?{applied} WHERE id = ?", (status, job_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def toggle_star(cfg: Config, job_id: str) -> int | None:
    conn = _open(cfg)
    try:
        cur = conn.execute(
            "UPDATE jobs SET starred = CASE starred WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (job_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return int(conn.execute("SELECT starred FROM jobs WHERE id = ?", (job_id,)).fetchone()[0])
    finally:
        conn.close()


def toggle_hide(cfg: Config, job_id: str) -> int | None:
    """Soft-hide/unhide (reversible; row stays so tracker dedup still matches)."""
    conn = _open(cfg)
    try:
        cur = conn.execute(
            "UPDATE jobs SET hidden = CASE IFNULL(hidden, 0) WHEN 1 THEN 0 ELSE 1 END "
            "WHERE id = ?",
            (job_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return int(conn.execute("SELECT hidden FROM jobs WHERE id = ?", (job_id,)).fetchone()[0])
    finally:
        conn.close()


def set_notes(cfg: Config, job_id: str, notes: str) -> bool:
    conn = _open(cfg)
    try:
        cur = conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete(cfg: Config, job_id: str) -> bool:
    conn = _open(cfg)
    try:
        conn.execute("DELETE FROM resumes WHERE job_id = ?", (job_id,))
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def bulk_set_status(cfg: Config, ids: list[str], status: str) -> int:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    if not ids:
        return 0
    conn = _open(cfg)
    try:
        applied = (
            ", applied_at = COALESCE(applied_at, datetime('now'))"
            if status == "applied"
            else ""
        )
        placeholders = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"UPDATE jobs SET status = ?{applied} WHERE id IN ({placeholders})",
            (status, *ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def latest_resume_path(cfg: Config, job_id: str) -> str | None:
    """The newest resume file_path for a job, or None."""
    if not db_exists(cfg):
        return None
    conn = _open(cfg)
    try:
        row = conn.execute(
            "SELECT file_path FROM resumes WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return row["file_path"] if row else None
    finally:
        conn.close()


# --- importing (used by handoff + the Chrome Claude text importer) -------


def _slugify(text: str, n: int) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())[:n] or "x"


def make_job_id(company: str, role: str, existing_ids: set[str]) -> str:
    """Readable unique id like `jstripe_backendeng`; uuid suffix on collision."""
    base = f"j{_slugify(company, 12)}_{_slugify(role, 10)}"
    if base not in existing_ids:
        return base
    return f"{base}_{uuid.uuid4().hex[:4]}"


def find_duplicate(
    cur: sqlite3.Cursor, company: str, job_id: str | None, role: str
) -> str | None:
    """Existing tracker id if this job is already tracked (hidden rows count).

    Match by (company, ATS job_id) first, then (company, role). Company and
    role compare case-insensitively.
    """
    if job_id:
        row = cur.execute(
            "SELECT id FROM jobs WHERE LOWER(company) = LOWER(?) AND job_id = ?",
            (company, job_id),
        ).fetchone()
        if row:
            return str(row[0])
    row = cur.execute(
        "SELECT id FROM jobs WHERE LOWER(company) = LOWER(?) AND LOWER(role) = LOWER(?)",
        (company, role),
    ).fetchone()
    return str(row[0]) if row else None


def under_company_cap(cur: sqlite3.Cursor, company: str, cap: int) -> bool:
    """True if fewer than `cap` visible, non-skip rows exist for this company."""
    n = cur.execute(
        "SELECT COUNT(*) FROM jobs WHERE LOWER(company) = LOWER(?) "
        "AND IFNULL(hidden, 0) = 0 AND IFNULL(status, '') != 'skip'",
        (company,),
    ).fetchone()[0]
    return int(n) < cap


def _row(j: dict[str, Any], batch: int, jid: str) -> tuple[Any, ...]:
    return (
        jid, batch, j.get("company"), j.get("role"), j.get("role_tier"),
        j.get("role_category"), j.get("job_id"), j.get("location"),
        j.get("location_pass"), j.get("posted"), j.get("level"), j.get("salary"),
        j.get("h1b"), j.get("url"), j.get("fit_score"), j.get("stack_score"),
        j.get("level_score"), j.get("function_score"), j.get("bridgeability"),
        j.get("desirability"), (j.get("verdict") or "CONSIDER").upper(),
        j.get("bridge_notes"), json.dumps(j.get("key_requirements") or []),
        json.dumps(j.get("key_nice_to_haves") or []), j.get("full_jd_text"),
        1 if j.get("starred") else 0, j.get("status") or "new",
        j.get("notes") or "", j.get("applied_at"),
    )


def _register_resume(
    cfg: Config, cur: sqlite3.Cursor, jid: str, resume_path: str, variant: str
) -> bool:
    """Record a resume .docx for a tracked job (copied into resumes_dir if
    it lives elsewhere). Returns False if the file is missing."""
    src = Path(resume_path).expanduser()
    if not src.exists():
        return False
    dest = src
    out_dir = resume_output_dir(cfg)
    if src.resolve().parent != out_dir.resolve():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / src.name
            shutil.copy2(src, dest)
        except OSError:
            dest = src
    cur.execute(
        "INSERT INTO resumes (job_id, resume_number, variant, file_path, generated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (jid, None, variant, str(dest), datetime.now().isoformat()),
    )
    return True


def insert_jobs(
    cfg: Config,
    jobs: list[dict[str, Any]],
    *,
    source: str = "discover",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Insert tracker-shaped job dicts with dedup + per-company cap.

    Each dict uses the `jobs` column names (company, role, fit_score, ...)
    plus optional `resume_path`. Returns counts:
    {batch, inserted, resumes, skipped: {duplicate, cap, invalid}}.
    """
    conn = _open(cfg)
    skipped = {"duplicate": 0, "cap": 0, "invalid": 0}
    inserted = 0
    resumes = 0
    try:
        cur = conn.cursor()
        existing_ids = {str(r[0]) for r in cur.execute("SELECT id FROM jobs")}
        batch = int(cur.execute("SELECT IFNULL(MAX(batch), 0) FROM jobs").fetchone()[0]) + 1
        cols = ", ".join(INSERT_COLUMNS)
        placeholders = ", ".join(["?"] * len(INSERT_COLUMNS))
        for j in jobs:
            company = str(j.get("company") or "").strip()
            role = str(j.get("role") or "").strip()
            if not company or not role:
                skipped["invalid"] += 1
                continue
            if find_duplicate(cur, company, j.get("job_id"), role):
                skipped["duplicate"] += 1
                continue
            if not under_company_cap(cur, company, cfg.tracker.max_per_company):
                skipped["cap"] += 1
                continue
            jid = make_job_id(company, role, existing_ids)
            existing_ids.add(jid)
            inserted += 1
            if dry_run:
                continue
            cur.execute(
                f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", _row(j, batch, jid)
            )
            rp = j.get("resume_path")
            if rp and _register_resume(cfg, cur, jid, str(rp), source):
                resumes += 1
        if not dry_run and inserted:
            cur.execute(
                "INSERT OR REPLACE INTO batches "
                "(batch_number, companies, roles_added, skipped, added_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (batch, source, inserted, sum(skipped.values())),
            )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"batch": batch, "inserted": inserted, "resumes": resumes, "skipped": skipped}
