"""SQLite-backed reports."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from patient.report import StoredReport

_UPSERT = """
INSERT INTO reports (id, patient_id, visit_id, kind, analysed, data, uploaded_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    visit_id   = excluded.visit_id,
    kind       = excluded.kind,
    analysed   = excluded.analysed,
    data       = excluded.data,
    updated_at = excluded.updated_at
"""


class SqliteReportRepository:
    """Satisfies storage.repository.ReportRepository."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, report_id: str) -> Optional[StoredReport]:
        row = self._conn.execute(
            "SELECT data FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        return StoredReport.model_validate_json(row["data"]) if row else None

    def list_for_patient(self, patient_id: str, limit: Optional[int] = None) -> list[StoredReport]:
        sql = "SELECT data FROM reports WHERE patient_id = ? ORDER BY uploaded_at DESC"
        params: tuple = (patient_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [StoredReport.model_validate_json(row["data"]) for row in rows]

    def list_for_visit(self, visit_id: str) -> list[StoredReport]:
        rows = self._conn.execute(
            "SELECT data FROM reports WHERE visit_id = ? ORDER BY uploaded_at",
            (visit_id,),
        ).fetchall()
        return [StoredReport.model_validate_json(row["data"]) for row in rows]

    def save(self, report: StoredReport) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                _UPSERT,
                (
                    report.id,
                    report.patient_id,
                    report.visit_id,
                    report.kind,
                    1 if report.is_analysed else 0,
                    report.model_dump_json(),
                    report.uploaded_at.isoformat(),
                    now,
                ),
            )
