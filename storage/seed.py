"""Load the fixture patients into the local record store.

Until the real database exists, this is what makes the clinical layer testable: a
handful of charts with the properties that actually matter downstream — a drug
allergy, an anticoagulant, an unfinished encounter awaiting results, and one patient
with no history at all to exercise the first-visit path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from config import SEED_FILE
from patient.profile import PatientProfile
from patient.visit import Visit
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.visit_repository import SqliteVisitRepository


def load_seed(path: Optional[Path] = None) -> tuple[list[PatientProfile], list[Visit]]:
    """Read and validate the fixture file without touching the database."""
    source = Path(path or SEED_FILE)
    if not source.exists():
        raise FileNotFoundError(f"Seed file not found: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    patients = [PatientProfile.model_validate(p) for p in raw.get("patients", [])]
    visits = [Visit.model_validate(v) for v in raw.get("visits", [])]
    return patients, visits


def seed(
    path: Optional[Path] = None,
    db_path: Optional[str] = None,
    reset: bool = False,
) -> tuple[int, int]:
    """Write the fixtures into the store. Returns (patients, visits) written.

    Patients are saved before visits because a visit's foreign key would otherwise be
    rejected — the ordering is enforced by the schema, not merely conventional.
    """
    patients, visits = load_seed(path)
    if reset:
        db.reset(db_path)

    conn = db.connect(db_path)
    try:
        patient_repo = SqlitePatientRepository(conn)
        visit_repo = SqliteVisitRepository(conn)
        for profile in patients:
            patient_repo.save(profile)
        for visit in visits:
            visit_repo.save(visit)
    finally:
        conn.close()
    return len(patients), len(visits)
