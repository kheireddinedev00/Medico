"""The request and response envelopes.

There is one request shape for almost everything: the chart. Laravel sends who the patient
is, what happened before, and the visit being worked on; the engine reads it, reasons, and
returns. It holds nothing between calls, so every request carries what it needs — which is
the same property `chatbot/consultation.py` already relies on, now visible over HTTP.

These models deliberately reuse the engine's own types rather than redeclaring them. A
`Visit` posted by Laravel is validated by the same class the CLI uses, with the same
`extra="forbid"`, so a field Laravel invents is rejected here instead of being silently
dropped and noticed three weeks later.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Investigation, PrescribedMedication, Visit
from triage.schema import TriageRequest


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chart(_Strict):
    """Everything the engine needs to know about one patient, for one request."""

    profile: PatientProfile
    # The visit being worked on. `resume` filters it out of the background itself, so
    # Laravel may send the patient's whole visit list in `history` without deduplicating.
    visit: Visit
    history: list[Visit] = []
    reports: list[StoredReport] = []


class VisitResult(_Strict):
    """What a transition returns.

    `persist` is the part that matters. The engine does not write a visit to the record
    until the physician has committed to a diagnosis — an assessment the doctor walks away
    from must leave nothing behind. That rule lives in `ConsultationSession`, and this flag
    is how it reaches Laravel without being reimplemented in PHP.

    Laravel stores the visit when `persist` is true, and discards it when it is false.
    """

    visit: Visit
    persist: bool


class StartRequest(_Strict):
    profile: PatientProfile
    history: list[Visit] = []
    reports: list[StoredReport] = []


class FindingsRequest(_Strict):
    chart: Chart
    # A partial Visit carrying what the doctor just recorded. Only the fields
    # `ConsultationSession.apply_findings` reads are used; the rest are ignored.
    findings: Visit


class DiagnosisRequest(_Strict):
    chart: Chart
    label: str
    icd10_code: Optional[str] = None
    reasoning: Optional[str] = None


class CodeRequest(_Strict):
    chart: Chart
    code: str


class InvestigationsRequest(_Strict):
    chart: Chart
    investigations: list[Investigation] = Field(min_length=1)


class ResultsRequest(_Strict):
    chart: Chart
    summary: str
    # Which ordered investigations actually came back. None means all of them, which is
    # only correct when the doctor has confirmed that — the CLI asks about each one.
    resulted: Optional[list[str]] = None


class ResultsFromReportsRequest(_Strict):
    chart: Chart
    report_ids: list[str] = Field(min_length=1)
    resulted: Optional[list[str]] = None


class PrescribeRequest(_Strict):
    chart: Chart
    medications: list[PrescribedMedication] = []


class FollowUpRequest(_Strict):
    chart: Chart
    plan: Optional[str] = None


class NoteRequest(_Strict):
    chart: Chart
    note: str


class ChartRequest(_Strict):
    """For the transitions and suggestions that need nothing but the chart."""

    chart: Chart


class MedicationCheckRequest(_Strict):
    """Drugs the physician chose themselves, put through the same screen.

    The safety screen exists because a model's suggestion cannot be trusted unchecked. A
    physician typing a drug name is a different act — they are the decision-maker, and the
    screen does not overrule them — but they are equally capable of not having the allergy
    list in front of them at that moment. So the check runs, and the answer is shown; what
    is done about it stays theirs.
    """

    chart: Chart
    names: list[str] = Field(min_length=1)


class AnalyseRequest(_Strict):
    """A stored extraction, sent back for the physician's explicit analysis step."""

    extracted: dict


class SoapRequest(_Strict):
    chart: Chart
    include_assistant_differential: bool = True


class TriageAssessRequest(_Strict):
    """One patient at the front desk.

    Note what this is NOT: a `Chart`. Triage happens before a visit exists, so there is
    nothing to send in `visit`, and requiring one would mean inventing an empty encounter
    for every walk-in. The triage form stands on its own.

    `profile` is the record when the patient is on file and null when they are not. The
    engine stores nothing, so a chart it is not sent is a chart it does not have - and
    the pipeline is built to work either way, because a waiting room is full of people
    nobody has registered yet.
    """

    request: TriageRequest
    profile: Optional[PatientProfile] = None
    # Skip the model and return the deterministic result alone. Laravel sets this when it
    # wants a guaranteed-fast answer; the rules produce a complete decision without it.
    rules_only: bool = False


class ErrorResponse(_Strict):
    error: str
    detail: str
