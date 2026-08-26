"""The permanent patient record.

This is the half of the clinical picture that does NOT change from visit to visit:
who the patient is, what they are allergic to, what they already take, what they
already have. Encounters live in `patient.visit`.

Design notes:
- Mirrors the strictness of report_reader.schema: extra="forbid" so a typo in a seed
  file or a stray key from a future backend fails loudly instead of being dropped.
- Everything clinically optional defaults to a value that says "not recorded" rather
  than to something that reads as a negative finding. `severity="unknown"` and
  `status="unknown"` exist for exactly this reason — an empty allergy list means
  "no known allergies", which is a real clinical statement, and it must not be
  confused with "nobody asked".
- Nothing here is respiratory-specific. The profile is general; the reasoning layers
  on top of it are what specialise.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

Severity = Literal["mild", "moderate", "severe", "unknown"]
Sex = Literal["male", "female", "other", "unknown"]
SmokingStatus = Literal["never", "former", "current", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Allergy(_Strict):
    """A recorded allergy or intolerance.

    `substance` is deliberately free text (e.g. "Penicillin", "Sulfonamides",
    "Contrast media"). Mapping it onto a drug-class ontology is what makes
    contraindication checking reliable, and that belongs in clinical/medications.py
    (Phase 5) — not here, where it would silently rewrite what the doctor typed.
    """

    substance: str
    reaction: Optional[str] = None
    severity: Severity = "unknown"


class Medication(_Strict):
    """A medication the patient is on, or was on."""

    name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    indication: Optional[str] = None
    started: Optional[str] = None  # free text: "2019", "2024-03-11", "6 months ago"
    active: bool = True


class ChronicCondition(_Strict):
    name: str
    since: Optional[str] = None
    notes: Optional[str] = None


class SmokingHistory(_Strict):
    status: SmokingStatus = "unknown"
    pack_years: Optional[float] = None
    quit_year: Optional[int] = None


class PatientProfile(_Strict):
    id: str
    full_name: str
    sex: Sex = "unknown"
    date_of_birth: Optional[date] = None
    # Fallback for records that carry an age but no date of birth, which is common when
    # data is transcribed from paper. `age` prefers date_of_birth when both are present.
    age_years: Optional[int] = None
    allergies: list[Allergy] = []
    medications: list[Medication] = []
    chronic_conditions: list[ChronicCondition] = []
    smoking: SmokingHistory = SmokingHistory()
    notes: Optional[str] = None

    @property
    def age(self) -> Optional[int]:
        """Age in whole years, computed from date_of_birth when available.

        Computed rather than stored so it can never go stale — a stored age silently
        becomes wrong the moment the patient has a birthday, and age drives real
        clinical thresholds (CURB-65, paediatric dosing).
        """
        if self.date_of_birth is None:
            return self.age_years
        today = date.today()
        had_birthday = (today.month, today.day) >= (
            self.date_of_birth.month,
            self.date_of_birth.day,
        )
        return today.year - self.date_of_birth.year - (0 if had_birthday else 1)

    def active_medications(self) -> list[Medication]:
        return [m for m in self.medications if m.active]
