"""Repositories that live for the length of one HTTP request.

`storage/repository.py` says the clinical layer depends only on three protocols, so
swapping SQLite for a web backend is one adapter. This is that adapter, and it turned out
to be smaller than expected: the backend sends the chart with the request, these three
classes hold it in a dict, and `ConsultationSession` cannot tell the difference.

The consequence is worth stating plainly, because it is the whole design of the service:
**nothing here outlives the request.** `save()` writes to a dictionary that is discarded
when the response is sent. The engine still calls it — the session persists after every
decision, exactly as it does against SQLite — but the durable copy is Laravel's, and the
route returns the mutated objects for Laravel to store.

That is what keeps clinical logic in Python and persistence in PHP, with neither doing the
other's job.
"""

from __future__ import annotations

from typing import Optional

from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Visit


class InMemoryPatients:
    """Satisfies `storage.repository.PatientRepository`."""

    def __init__(self, profiles: list[PatientProfile]):
        self._by_id: dict[str, PatientProfile] = {p.id: p for p in profiles}

    def get(self, patient_id: str) -> Optional[PatientProfile]:
        return self._by_id.get(patient_id)

    def list_all(self) -> list[PatientProfile]:
        return list(self._by_id.values())

    def save(self, profile: PatientProfile) -> None:
        self._by_id[profile.id] = profile


class InMemoryVisits:
    """Satisfies `storage.repository.VisitRepository`."""

    def __init__(self, visits: list[Visit]):
        self._by_id: dict[str, Visit] = {v.id: v for v in visits}

    def get(self, visit_id: str) -> Optional[Visit]:
        return self._by_id.get(visit_id)

    def list_for_patient(self, patient_id: str, limit: Optional[int] = None) -> list[Visit]:
        """Newest first, as the protocol requires — the context builder relies on it."""
        found = [v for v in self._by_id.values() if v.patient_id == patient_id]
        found.sort(key=lambda v: v.created_at, reverse=True)
        return found[:limit] if limit else found

    def save(self, visit: Visit) -> None:
        self._by_id[visit.id] = visit


class InMemoryReports:
    """Satisfies `storage.repository.ReportRepository`."""

    def __init__(self, reports: list[StoredReport]):
        self._by_id: dict[str, StoredReport] = {r.id: r for r in reports}

    def get(self, report_id: str) -> Optional[StoredReport]:
        return self._by_id.get(report_id)

    def list_for_patient(
        self, patient_id: str, limit: Optional[int] = None
    ) -> list[StoredReport]:
        found = [r for r in self._by_id.values() if r.patient_id == patient_id]
        found.sort(key=lambda r: r.uploaded_at, reverse=True)
        return found[:limit] if limit else found

    def list_for_visit(self, visit_id: str) -> list[StoredReport]:
        """Oldest first here, newest first above. That asymmetry is the protocol's."""
        found = [r for r in self._by_id.values() if r.visit_id == visit_id]
        found.sort(key=lambda r: r.uploaded_at)
        return found
