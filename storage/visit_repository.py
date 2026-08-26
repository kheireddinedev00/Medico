"""SQLite-backed visits (the patient timeline)."""

from __future__ import annotations

import sqlite3
from typing import Optional

from patient.visit import Visit

_UPSERT = """
INSERT INTO visits (id, patient_id, status, data, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    status     = excluded.status,
    data       = excluded.data,
    updated_at = excluded.updated_at
"""


class SqliteVisitRepository:
    """Satisfies storage.repository.VisitRepository."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, visit_id: str) -> Optional[Visit]:
        row = self._conn.execute(
            "SELECT data FROM visits WHERE id = ?", (visit_id,)
        ).fetchone()
        return Visit.model_validate_json(row["data"]) if row else None

    def list_for_patient(self, patient_id: str, limit: Optional[int] = None) -> list[Visit]:
        sql = "SELECT data FROM visits WHERE patient_id = ? ORDER BY created_at DESC"
        params: tuple = (patient_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [Visit.model_validate_json(row["data"]) for row in rows]

    def save(self, visit: Visit) -> None:
        visit.touch()
        with self._conn:
            self._conn.execute(
                _UPSERT,
                (
                    visit.id,
                    visit.patient_id,
                    visit.status.value,
                    visit.model_dump_json(),
                    visit.created_at.isoformat(),
                    visit.updated_at.isoformat(),
                ),
            )
