"""SQLite-backed patient profiles."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from patient.profile import PatientProfile

_UPSERT = """
INSERT INTO patients (id, full_name, sex, date_of_birth, data, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    full_name     = excluded.full_name,
    sex           = excluded.sex,
    date_of_birth = excluded.date_of_birth,
    data          = excluded.data,
    updated_at    = excluded.updated_at
"""


class SqlitePatientRepository:
    """Satisfies storage.repository.PatientRepository."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, patient_id: str) -> Optional[PatientProfile]:
        row = self._conn.execute(
            "SELECT data FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
        return PatientProfile.model_validate_json(row["data"]) if row else None

    def list_all(self) -> list[PatientProfile]:
        rows = self._conn.execute("SELECT data FROM patients ORDER BY full_name").fetchall()
        return [PatientProfile.model_validate_json(row["data"]) for row in rows]

    def save(self, profile: PatientProfile) -> None:
        now = datetime.now(timezone.utc).isoformat()
        dob = profile.date_of_birth.isoformat() if profile.date_of_birth else None
        with self._conn:
            self._conn.execute(
                _UPSERT,
                (
                    profile.id,
                    profile.full_name,
                    profile.sex,
                    dob,
                    profile.model_dump_json(),
                    now,
                    now,
                ),
            )
