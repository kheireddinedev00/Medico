"""SQLite-backed triage results and waiting room.

Same strategy as the rest of `storage/`: queryable fields get real columns, the whole
Pydantic model is kept verbatim in a JSON `data` column, and the model is the schema of
record. A row that no longer validates fails loudly on read.

One difference from the other repositories: `save_result` is an INSERT, not an upsert.
A triage result is a statement about a moment, and a moment does not get edited. Saving
the same request id twice is a bug in the caller, and it raises rather than quietly
replacing the earlier decision - which, in an audit trail, is the thing you least want
to happen silently.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from triage.queue import WaitingRoomEntry
from triage.schema import TriageResult

_INSERT_RESULT = """
INSERT INTO triage_results (
    id, patient_id, priority, rule_priority, ai_escalated, news2_aggregate,
    status, ruleset_version, model, data, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPSERT_ENTRY = """
INSERT INTO waiting_room (key, patient_id, priority, arrived_at, triaged_at, removed_at, data)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(key) DO UPDATE SET
    patient_id = excluded.patient_id,
    priority   = excluded.priority,
    triaged_at = excluded.triaged_at,
    removed_at = excluded.removed_at,
    data       = excluded.data
"""


class DuplicateTriageResult(RuntimeError):
    """A triage result with this id is already recorded."""


class SqliteTriageRepository:
    """Satisfies triage.repository.TriageRepository."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # --- results -----------------------------------------------------------------

    def save_result(self, result: TriageResult) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    _INSERT_RESULT,
                    (
                        result.request_id,
                        result.patient_id,
                        result.priority.value,
                        result.rule_priority.value,
                        int(result.ai_escalated),
                        result.news2.aggregate if result.news2 else None,
                        result.status,
                        result.ruleset_version,
                        result.model,
                        result.model_dump_json(),
                        result.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateTriageResult(
                f"A triage result for request {result.request_id} is already recorded. "
                f"A re-triage must be a new request, not a rewrite of an old decision."
            ) from exc

    def get_result(self, request_id: str) -> Optional[TriageResult]:
        row = self._conn.execute(
            "SELECT data FROM triage_results WHERE id = ?", (request_id,)
        ).fetchone()
        return TriageResult.model_validate_json(row["data"]) if row else None

    def list_for_patient(
        self, patient_id: str, limit: Optional[int] = None
    ) -> list[TriageResult]:
        sql = "SELECT data FROM triage_results WHERE patient_id = ? ORDER BY created_at DESC"
        params: tuple = (patient_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [TriageResult.model_validate_json(row["data"]) for row in rows]

    def recent(self, limit: int = 50) -> list[TriageResult]:
        """The newest decisions across all patients. For the evaluation dashboard."""
        rows = self._conn.execute(
            "SELECT data FROM triage_results ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [TriageResult.model_validate_json(row["data"]) for row in rows]

    # --- waiting room ------------------------------------------------------------

    def save_entry(self, key: str, entry: WaitingRoomEntry) -> None:
        with self._conn:
            self._conn.execute(
                _UPSERT_ENTRY,
                (
                    key,
                    entry.patient_id,
                    entry.priority.value,
                    entry.arrived_at.isoformat(),
                    entry.triaged_at.isoformat(),
                    entry.removed_at.isoformat() if entry.removed_at else None,
                    entry.model_dump_json(),
                ),
            )

    def load_room(self, include_removed: bool = False) -> dict[str, WaitingRoomEntry]:
        sql = "SELECT key, data FROM waiting_room"
        if not include_removed:
            sql += " WHERE removed_at IS NULL"
        sql += " ORDER BY arrived_at"
        rows = self._conn.execute(sql).fetchall()
        return {
            row["key"]: WaitingRoomEntry.model_validate_json(row["data"]) for row in rows
        }
