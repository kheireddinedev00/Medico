"""A single clinical encounter.

One `Visit` is one row on the patient's timeline. It holds what the doctor observed,
what the assistant proposed, and — kept separate — what the doctor actually decided.

That separation is the point of this module. `differential` is the assistant's
suggestion; `working_diagnosis` is the doctor's choice. `Investigation.rationale`
records why the assistant suggested a test, but a test only appears in
`ordered_investigations` because the doctor selected it. Nothing in this phase, and
nothing in any later phase, writes a decision field on the AI's behalf.

Phase 1 stores and retrieves visits. Advancing them through the states in
clinical.consultation_state is Phase 4.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from clinical.consultation_state import VisitStatus

InvestigationCategory = Literal["laboratory", "radiology", "other"]
InvestigationStatus = Literal["ordered", "resulted", "cancelled"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    """UTC, always. Display in local time is a presentation concern."""
    return datetime.now(timezone.utc)


class Vitals(_Strict):
    """Recorded as measured. No unit conversion, no derived scores.

    blood_pressure stays a string ("128/76") rather than two integers because that is
    how it is written on a chart, and splitting it invites a parse error on entries
    like "128/76 (left arm)".
    """

    temperature_c: Optional[float] = None
    heart_rate: Optional[int] = None
    respiratory_rate: Optional[int] = None
    blood_pressure: Optional[str] = None
    spo2: Optional[float] = None  # percent, room air unless noted in observations
    weight_kg: Optional[float] = None


class Diagnosis(_Strict):
    label: str
    icd10_code: Optional[str] = None
    # Free text, not a number. A percentage here would be false precision — the model
    # has no calibrated basis for one, and doctors read "72%" as if it meant something.
    likelihood: Optional[str] = None
    reasoning: Optional[str] = None


class Investigation(_Strict):
    name: str
    category: InvestigationCategory = "other"
    rationale: Optional[str] = None
    status: InvestigationStatus = "ordered"


class PrescribedMedication(_Strict):
    name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    rationale: Optional[str] = None


class Visit(_Strict):
    id: str = Field(default_factory=lambda: _new_id("v"))
    patient_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    status: VisitStatus = VisitStatus.INITIAL_ASSESSMENT

    # --- What the doctor collected during the consultation ---
    chief_complaint: Optional[str] = None
    symptoms: list[str] = []
    vitals: Vitals = Vitals()
    physical_exam: Optional[str] = None
    observations: Optional[str] = None
    # What came back from the investigations, kept in its own field rather than appended
    # to `observations`. Results decide a differential, and a sentence buried in a
    # paragraph of examination notes does not read as decisive to the model.
    results_summary: Optional[str] = None

    # --- What the assistant proposed (suggestions, never decisions) ---
    differential: list[Diagnosis] = []

    # --- What the doctor decided ---
    working_diagnosis: Optional[Diagnosis] = None
    ordered_investigations: list[Investigation] = []
    prescribed_medications: list[PrescribedMedication] = []
    doctor_notes: Optional[str] = None

    # Structured report_reader output attached to this visit. Populated in Phase 6;
    # the field exists now so the timeline shape does not change under you later.
    report_ids: list[str] = []

    def touch(self) -> None:
        self.updated_at = _now()
