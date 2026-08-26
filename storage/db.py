"""SQLite connection and schema for the local medical record.

Storage strategy: identifying and queryable fields get real columns; the full
Pydantic model is kept verbatim in a JSON `data` column. This keeps the schema stable
while the clinical models are still evolving — adding a field to `Visit` in Phase 4
does not require a migration — while still allowing indexed lookups on the things the
application actually filters by.

The Pydantic model, not the table, is the schema of record. Anything read back out is
validated against it, so a hand-edited row that no longer matches fails loudly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id            TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    sex           TEXT,
    date_of_birth TEXT,
    data          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    id         TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visits_patient
    ON visits(patient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    -- Nullable on purpose: results arrive late, out of order, and sometimes for
    -- nothing anyone ordered.
    visit_id    TEXT REFERENCES visits(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    analysed    INTEGER NOT NULL DEFAULT 0,
    data        TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_patient
    ON reports(patient_id, uploaded_at DESC);

CREATE INDEX IF NOT EXISTS idx_reports_visit ON reports(visit_id);
"""


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open the record store, creating the file and schema on first use."""
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Off by default in sqlite3; without it the visits -> patients reference is
    # decorative and an orphaned visit could be written.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def reset(db_path: Optional[str] = None) -> None:
    """Drop all clinical data. Used by the seeder; never call this from the app."""
    conn = connect(db_path)
    with conn:
        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM visits")
        conn.execute("DELETE FROM patients")
    conn.close()
