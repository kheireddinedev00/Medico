"""The seam between the clinical layer and wherever patient data actually lives.

Everything above this line (patient.history, and the clinical services in later
phases) depends only on these two protocols. Today they are satisfied by the SQLite
implementations in this package. When the web backend is ready, an adapter that calls
your teammate's API satisfies exactly the same protocols and nothing in the clinical
layer changes — that is the whole reason this file exists rather than the clinical
code importing sqlite3 directly.

Protocols rather than base classes: an implementation only has to match the shape, so
a fake repository in a test is three methods and no inheritance.
"""

from __future__ import annotations

from typing import Optional, Protocol

from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Visit


class PatientRepository(Protocol):
    def get(self, patient_id: str) -> Optional[PatientProfile]:
        """Return the profile, or None if no such patient exists."""
        ...

    def list_all(self) -> list[PatientProfile]:
        ...

    def save(self, profile: PatientProfile) -> None:
        """Insert or replace the whole profile."""
        ...


class VisitRepository(Protocol):
    def get(self, visit_id: str) -> Optional[Visit]:
        ...

    def list_for_patient(self, patient_id: str, limit: Optional[int] = None) -> list[Visit]:
        """Visits for one patient, newest first."""
        ...

    def save(self, visit: Visit) -> None:
        ...


class ReportRepository(Protocol):
    def get(self, report_id: str) -> Optional[StoredReport]:
        ...

    def list_for_patient(self, patient_id: str, limit: Optional[int] = None) -> list[StoredReport]:
        """Reports for one patient, newest first."""
        ...

    def list_for_visit(self, visit_id: str) -> list[StoredReport]:
        """Reports attached to one visit, oldest first."""
        ...

    def save(self, report: StoredReport) -> None:
        ...
