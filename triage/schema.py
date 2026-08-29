"""What goes into a triage decision, and what comes out.

This module is the contract for the whole agent. It deliberately imports nothing from
`patient`, `clinical`, `storage` or any LLM library: a triage request is a form a nurse
filled in, and it must be constructible from a JSON body posted by a backend that knows
nothing about this project's record models. `triage/service.py` is where the two worlds
meet.

Three shapes matter:

`TriageRequest`   what was measured and what the patient said.
`NormalisedVitals` the same thing after parsing and unit checks, with every field either
                  a usable number or None. Nothing downstream reads the raw request.
`TriageResult`    the decision, plus every input to it, so a clinician can see why.

On the absence of a 0-100 score: `news2_score` is a real number from a published scale
and is reported. There is no invented composite score and no model-supplied confidence,
for the same reason `clinical/differential.py` rejects percentage likelihoods - a number
a clinician cannot trace back to a rule reads as precision the system does not have.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Consciousness = Literal["alert", "confusion", "voice", "pain", "unresponsive", "unknown"]
Sex = Literal["male", "female", "other", "unknown"]
TriageStatus = Literal["OK", "INSUFFICIENT_DATA", "OUT_OF_SCOPE"]
Severity = Literal["error", "warning"]


class _Strict(BaseModel):
    """extra='forbid', as everywhere else in this project.

    A backend that posts `spo2_percent` instead of `spo2` gets a 422 naming the field,
    rather than a triage decision computed without an oxygen saturation.
    """

    model_config = ConfigDict(extra="forbid")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------
# Priority
# --------------------------------------------------------------------------------


class Priority(str, Enum):
    """The four levels, ordered by RANK below rather than by declaration.

    `str` mixin so it serialises as a plain string, matching `VisitStatus`.
    """

    CRITICAL = "CRITICAL"
    URGENT = "URGENT"
    STANDARD = "STANDARD"
    LOW = "LOW"


# Higher rank = seen sooner. Comparison goes through this table rather than through the
# enum's own ordering, because `Priority.CRITICAL < Priority.LOW` alphabetically is true
# and would silently invert the queue.
RANK: dict[Priority, int] = {
    Priority.LOW: 0,
    Priority.STANDARD: 1,
    Priority.URGENT: 2,
    Priority.CRITICAL: 3,
}


def rank(priority: Priority) -> int:
    return RANK[priority]


def highest(*priorities: Optional[Priority]) -> Priority:
    """The most urgent of the given priorities; LOW when none are given.

    This is the function that enforces "the rules are a floor". Every combination of a
    rule outcome with a model outcome goes through here, so there is exactly one place
    in the system where a priority can be decided, and it can only ever go up.
    """
    present = [p for p in priorities if p is not None]
    if not present:
        return Priority.LOW
    return max(present, key=rank)


# --------------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------------


class TriageVitals(_Strict):
    """Vital signs as recorded at the front desk.

    `blood_pressure` is accepted as the string a chart carries ("128/76") because that
    is what `patient.visit.Vitals` stores and what a nurse types. It is parsed in
    `triage.validation`, not here, so an unparseable entry becomes a reported data
    quality problem instead of a 422 that loses the rest of the observations.
    Structured `systolic_bp` / `diastolic_bp` are accepted too and win when both forms
    are present.
    """

    temperature_c: Optional[float] = None
    heart_rate: Optional[int] = None
    respiratory_rate: Optional[int] = None
    blood_pressure: Optional[str] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    spo2: Optional[float] = None
    # Supplemental oxygen is a NEWS2 parameter in its own right. None means nobody
    # recorded it, which is NOT the same as "on air" and is not scored as such.
    on_oxygen: Optional[bool] = None
    oxygen_delivery: Optional[str] = None  # free text: "2 L/min nasal cannulae"
    consciousness: Consciousness = "unknown"
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None

    def any_recorded(self) -> bool:
        recorded = self.model_dump(exclude={"consciousness"})
        return any(v is not None for v in recorded.values()) or self.consciousness != "unknown"


class TriageRequest(_Strict):
    """One patient presenting at the front desk."""

    id: str = Field(default_factory=lambda: f"t-{uuid.uuid4().hex[:12]}")
    # Present when the patient is on file. Absent for a walk-in, which must work.
    patient_id: Optional[str] = None
    arrived_at: datetime = Field(default_factory=_now)

    age_years: Optional[int] = None
    sex: Sex = "unknown"
    # Tri-state on purpose. False is a recorded negative; None is "nobody asked", and
    # the two lead to different handling at the scope check.
    is_pregnant: Optional[bool] = None
    # Selects NEWS2 Scale 2. Only ever set from a documented prescribed target range of
    # 88-92%, never inferred from a COPD diagnosis - see triage/news2.py.
    hypercapnic_target_range: bool = False

    # What the waiting-room board shows. A label, not an identity: for a walk-in it is
    # whatever the desk wrote down, and it is never used to look anything up.
    display_name: Optional[str] = None

    chief_complaint: Optional[str] = None
    symptoms: list[str] = []
    notes: Optional[str] = None

    vitals: TriageVitals = TriageVitals()

    # A plain-text projection of the medical record, built by triage/service.py when the
    # patient is on file. Kept as text so this module stays independent of the record
    # models and so the backend can supply its own.
    chart_summary: Optional[str] = None

    recorded_by: Optional[str] = None

    def presentation_text(self) -> str:
        """Complaint, symptoms and notes as one lowercase blob, for flag matching."""
        parts = [self.chief_complaint or "", " ".join(self.symptoms), self.notes or ""]
        return " ".join(p for p in parts if p).lower()


class NormalisedVitals(_Strict):
    """Vitals after parsing and plausibility checks. Every field is usable or None.

    Downstream code reads this and never the raw request, so there is exactly one place
    where a string becomes a number and one place where an implausible value is dropped.
    """

    temperature_c: Optional[float] = None
    heart_rate: Optional[int] = None
    respiratory_rate: Optional[int] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    spo2: Optional[float] = None
    on_oxygen: Optional[bool] = None
    consciousness: Consciousness = "unknown"
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None


# --------------------------------------------------------------------------------
# Intermediate findings
# --------------------------------------------------------------------------------


class DataIssue(_Strict):
    """One problem with the data, in words a person can act on."""

    field: str
    issue: Literal["missing", "implausible", "unparseable", "unit_suspect", "contradictory"]
    severity: Severity = "warning"
    detail: str

    def describe(self) -> str:
        return f"{self.field}: {self.detail}"


class ValidationReport(_Strict):
    normalised: NormalisedVitals = NormalisedVitals()
    issues: list[DataIssue] = []
    # Fields the rule set names as required for a full score that were not usable.
    missing_required: list[str] = []
    # True when the patient falls outside the validated population.
    out_of_scope: bool = False
    out_of_scope_reason: Optional[str] = None

    def errors(self) -> list[DataIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def descriptions(self) -> list[str]:
        return [i.describe() for i in self.issues]


class ParameterScore(_Strict):
    """One NEWS2 parameter, scored. The audit trail for the aggregate."""

    parameter: str
    label: str
    value: Optional[str] = None
    score: Optional[int] = None  # None when the observation was missing
    scored: bool = False

    def describe(self) -> str:
        if not self.scored:
            return f"{self.label}: not recorded"
        return f"{self.label}: {self.value} (scores {self.score})"


class News2Result(_Strict):
    aggregate: int = 0
    parameters: list[ParameterScore] = []
    # NEWS2's own red-score rule: any single parameter scoring 3.
    single_parameter_red: bool = False
    red_parameters: list[str] = []
    scored_count: int = 0
    expected_count: int = 7
    scale: Literal[1, 2] = 1

    def complete(self) -> bool:
        return self.scored_count >= self.expected_count

    def abnormal(self) -> list[ParameterScore]:
        return [p for p in self.parameters if p.scored and (p.score or 0) > 0]


class RedFlagHit(_Strict):
    id: str
    label: str
    floor: Priority
    matched: str
    reason: str
    source: Optional[str] = None
    # "rules" when the deterministic matcher found it, "ai" when the model did.
    detected_by: Literal["rules", "ai"] = "rules"


# --------------------------------------------------------------------------------
# The model's contribution
# --------------------------------------------------------------------------------


class RawInterpretation(_Strict):
    """Exactly what the model is permitted to return, and nothing more.

    Note what is absent: no priority, no score, no diagnosis, no confidence. The model
    contributes risk signals and language; the priority arithmetic stays in code. It
    cannot return a priority because there is no field for one.
    """

    risk_signals: list[str] = []
    escalate: bool = False
    escalation_reason: Optional[str] = None
    concerning_findings: list[str] = []
    missing_information: list[str] = []
    context_factors: list[str] = []
    summary: Optional[str] = None


class Interpretation(_Strict):
    """The model's contribution after it has been checked and bounded."""

    available: bool = False
    risk_signals: list[str] = []
    escalate: bool = False
    escalation_reason: Optional[str] = None
    concerning_findings: list[str] = []
    missing_information: list[str] = []
    context_factors: list[str] = []
    summary: Optional[str] = None
    # Set when the model was asked and could not answer. The result stays valid.
    failure: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = None


# --------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------


class ClinicianOverride(_Strict):
    """A human changing the priority. Never overwrites the AI record; sits beside it."""

    priority: Priority
    reason: str
    clinician: Optional[str] = None
    at: datetime = Field(default_factory=_now)


class TriageResult(_Strict):
    """The decision and everything that produced it."""

    request_id: str
    patient_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)

    status: TriageStatus = "OK"
    priority: Priority = Priority.STANDARD
    recommended_action: str = ""

    # The three inputs to `priority`, kept separately so the split is auditable.
    rule_priority: Priority = Priority.LOW
    red_flag_priority: Optional[Priority] = None
    ai_escalated: bool = False

    news2: Optional[News2Result] = None
    red_flags: list[RedFlagHit] = []

    reasons: list[str] = []
    concerning_findings: list[str] = []
    missing_information: list[str] = []
    data_quality_issues: list[str] = []
    context_factors: list[str] = []

    # Always True. It is a field rather than a constant so a backend can render it
    # without knowing the policy, and so the day someone proposes turning it off, the
    # change shows up in a diff.
    requires_human_review: bool = True

    interpretation: Interpretation = Interpretation()
    override: Optional[ClinicianOverride] = None

    ruleset_version: str = ""
    engine_version: str = ""
    model: Optional[str] = None
    latency_ms: Optional[int] = None

    # The priority as a sortable integer, highest = seen first.
    #
    # Serialised with every result so a caller can `ORDER BY priority_rank DESC,
    # arrived_at ASC` without reimplementing the ladder. Without it the backend has to map
    # four strings onto an order in its own language, and that mapping is exactly the kind
    # of clinical policy this project keeps in one place.
    #
    # A stored field rather than a computed one, because a computed field serialises but
    # cannot be read back into a model declared extra="forbid" - which broke every
    # round-trip through the repository. `_sync_rank` below makes it behave like a
    # computed one anyway: whatever a caller supplies is overwritten from `priority`, so
    # the two cannot disagree.
    #
    # This is the AI's own priority. A clinician override is applied by whoever holds the
    # queue - the engine is stateless and never sees one.
    priority_rank: int = 0

    @model_validator(mode="after")
    def _sync_rank(self) -> "TriageResult":
        """Derive the rank from the priority, always, ignoring anything passed in.

        Runs on construction and on every re-validation, so a hand-edited row whose rank
        no longer matches its priority is corrected on read rather than sorting wrongly
        for the rest of the day.
        """
        expected = RANK[self.priority]
        if self.priority_rank != expected:
            self.priority_rank = expected
        return self

    def effective_priority(self) -> Priority:
        """What the queue sorts on: the clinician's call when they made one."""
        return self.override.priority if self.override else self.priority

    def is_degraded(self) -> bool:
        """True when the model was unavailable, so the result is rules-only."""
        return not self.interpretation.available
