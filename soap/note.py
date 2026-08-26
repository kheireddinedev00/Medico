"""Builds a SOAP note for one visit out of what is already in the record.

Everything a SOAP note needs was captured during the consultation, so this module
reasons about nothing. It is a projection — profile plus visit plus that visit's
reports, rearranged under the four headings a clinician expects. That is deliberate:

**No model is involved.** `patient/history.py` keeps the LLM out of chart rendering
because a second model summarising the chart is a second place for an allergy to get
lost. The same argument applies with more force here, because a SOAP note is a document
a physician may put their name on. Every string below can be traced to a field in the
database, so there is nothing in the note to fact-check.

**Suggestion stays separated from decision.** `visit.working_diagnosis` is the
assessment. `visit.differential` — what the assistant proposed — is carried in its own
field and rendered under its own heading, never merged into the assessment. The record
works hard to keep those apart (see `clinical/session.py`); a note that blurred them
would undo that at the last step.

**Nothing is stored.** The note is rebuilt from the record on each request, so it cannot
go stale against the chart it describes. There is no note table and no note id.

`build_soap_note` is a pure function over typed objects, exactly like
`build_clinical_context`. It touches no database, so the web backend can call it with
data from an API and get the same result, and tests can exercise it with fixtures.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Visit

# Absent data says it is absent. "Not recorded" and a negative finding are different
# clinical statements, and a note is exactly the wrong place to conflate them.
NOT_RECORDED = "Not recorded."
NOT_YET_RECORDED = "Not yet recorded — the encounter is still open at {status}."
NOT_ANALYSED = "Uploaded but not analysed."

ASSISTANT_HEADING = "Assistant-suggested differential (not the clinician's assessment)"

DISCLAIMER = (
    "Assembled from the medical record. Not a signed clinical document — review, "
    "amend and sign before it enters the chart."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportFinding(_Strict):
    """One report attached to this visit, as it appears under Objective."""

    label: str
    analysed: bool = False
    summary: Optional[str] = None
    abnormal: list[str] = []
    patterns: list[str] = []


class SubjectiveSection(_Strict):
    """What the patient reported, plus the background that frames it."""

    chief_complaint: Optional[str] = None
    symptoms: list[str] = []
    allergies: list[str] = []
    current_medications: list[str] = []
    past_medical_history: list[str] = []
    social_history: Optional[str] = None
    previous_diagnoses: list[str] = []
    previous_visit_count: int = 0
    background_notes: Optional[str] = None


class ObjectiveSection(_Strict):
    """What was measured and observed."""

    vitals: list[str] = []
    physical_exam: Optional[str] = None
    # `visit.observations` lands here rather than under Subjective. The field is free
    # text and could be either, so it keeps the label the doctor entered it under
    # instead of being silently reclassified as the patient's account.
    observations: Optional[str] = None
    investigation_results: Optional[str] = None
    reports: list[ReportFinding] = []


class AssessmentSection(_Strict):
    """The physician's conclusion, and — kept apart — the assistant's suggestion."""

    working_diagnosis: Optional[str] = None
    icd10_code: Optional[str] = None
    reasoning: Optional[str] = None
    assistant_differential: list[str] = []


class PlanSection(_Strict):
    """What was ordered, prescribed and arranged."""

    investigations: list[str] = []
    medications: list[str] = []
    notes: Optional[str] = None
    visit_status: str = ""


class SoapNote(_Strict):
    """One encounter under four headings.

    Kept as a typed object rather than only a string so an HTTP layer can return it as
    JSON and tests can assert on structure rather than on formatting.
    """

    patient_id: str
    patient_name: str
    patient_summary: str
    visit_id: str
    visit_date: date
    visit_status: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    subjective: SubjectiveSection = SubjectiveSection()
    objective: ObjectiveSection = ObjectiveSection()
    assessment: AssessmentSection = AssessmentSection()
    plan: PlanSection = PlanSection()

    def to_text(self) -> str:
        from soap.render import to_text

        return to_text(self)

    def to_markdown(self) -> str:
        from soap.render import to_markdown

        return to_markdown(self)


# --------------------------------------------------------------------------------
# Field rendering
# --------------------------------------------------------------------------------


def _demographics(profile: PatientProfile) -> str:
    """Age and sex, keeping a missing one visibly missing."""
    age = profile.age
    sex_known = profile.sex != "unknown"
    if age is not None and sex_known:
        return f"{age}-year-old {profile.sex}"
    if age is not None:
        return f"{age}-year-old, sex not recorded"
    if sex_known:
        return f"{profile.sex}, age not recorded"
    return "age and sex not recorded"


def _allergies(profile: PatientProfile) -> list[str]:
    lines = []
    for allergy in profile.allergies:
        detail = [allergy.reaction] if allergy.reaction else []
        if allergy.severity != "unknown":
            detail.append(allergy.severity)
        suffix = f" — {', '.join(detail)}" if detail else ""
        lines.append(f"{allergy.substance}{suffix}")
    return lines


def _medications(profile: PatientProfile) -> list[str]:
    lines = []
    for med in profile.active_medications():
        line = " ".join(p for p in [med.name, med.dose, med.frequency] if p)
        if med.indication:
            line += f" (for {med.indication})"
        lines.append(line)
    return lines


def _conditions(profile: PatientProfile) -> list[str]:
    lines = []
    for cond in profile.chronic_conditions:
        line = cond.name
        if cond.since:
            line += f" (since {cond.since})"
        if cond.notes:
            line += f" — {cond.notes}"
        lines.append(line)
    return lines


def _smoking(profile: PatientProfile) -> Optional[str]:
    smoking = profile.smoking
    if smoking.status == "unknown":
        return None
    if smoking.status == "never":
        return "Never smoker."
    parts = [f"{smoking.status} smoker"]
    if smoking.pack_years is not None:
        parts.append(f"{smoking.pack_years:g} pack-years")
    if smoking.quit_year is not None:
        parts.append(f"quit {smoking.quit_year}")
    return ", ".join(parts) + "."


def _vitals(visit: Visit) -> list[str]:
    v = visit.vitals
    lines = []
    if v.temperature_c is not None:
        lines.append(f"Temperature {v.temperature_c:g} °C")
    if v.heart_rate is not None:
        lines.append(f"Heart rate {v.heart_rate}/min")
    if v.respiratory_rate is not None:
        lines.append(f"Respiratory rate {v.respiratory_rate}/min")
    if v.blood_pressure:
        lines.append(f"Blood pressure {v.blood_pressure} mmHg")
    if v.spo2 is not None:
        lines.append(f"SpO2 {v.spo2:g}%")
    if v.weight_kg is not None:
        lines.append(f"Weight {v.weight_kg:g} kg")
    return lines


def _previous_diagnoses(history: list[Visit]) -> list[str]:
    """Every previous working diagnosis, newest first, respiratory or not.

    Comorbidities are the point: heart failure and anaemia both present as
    breathlessness, and filtering them out of the background would hide the reason a
    respiratory diagnosis was or was not reached.
    """
    lines = []
    for visit in sorted(history, key=lambda v: v.created_at, reverse=True):
        diagnosis = visit.working_diagnosis
        if diagnosis is None:
            continue
        code = f" [{diagnosis.icd10_code}]" if diagnosis.icd10_code else ""
        lines.append(f"{visit.created_at.date()} — {diagnosis.label}{code}")
    return lines


def _report_finding(report: StoredReport) -> ReportFinding:
    """One report as it reads under Objective.

    An unanalysed report is listed as exactly that rather than omitted. "No imaging on
    file" and "the chest film is sitting there unread" are different situations, and
    only one of them is reassuring.
    """
    if report.analysis is None:
        return ReportFinding(label=report.label(), analysed=False)
    return ReportFinding(
        label=report.label(),
        analysed=True,
        summary=report.analysis.summary or None,
        abnormal=[r.describe() for r in report.analysis.abnormal()],
        patterns=list(report.analysis.patterns),
    )


def _investigations(visit: Visit) -> list[str]:
    return [
        f"{i.name} ({i.category}, {i.status})"
        + (f" — {i.rationale}" if i.rationale else "")
        for i in visit.ordered_investigations
    ]


def _prescriptions(visit: Visit) -> list[str]:
    lines = []
    for med in visit.prescribed_medications:
        line = " ".join(p for p in [med.name, med.dose, med.frequency, med.duration] if p)
        if med.rationale:
            line += f" — {med.rationale}"
        lines.append(line)
    return lines


def _differential(visit: Visit) -> list[str]:
    """The assistant's ranked list, in the order it produced.

    `likelihood` is free text by design — the record refuses to store a percentage,
    because the model has no calibrated basis for one and a physician reads "72%" as if
    it meant something.
    """
    lines = []
    for i, diagnosis in enumerate(visit.differential, 1):
        line = f"{i}. {diagnosis.label}"
        if diagnosis.icd10_code:
            line += f" [{diagnosis.icd10_code}]"
        if diagnosis.likelihood:
            line += f" — {diagnosis.likelihood}"
        lines.append(line)
    return lines


# --------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------


def reports_for_visit(visit: Visit, reports: list[StoredReport]) -> list[StoredReport]:
    """The reports belonging to this encounter, oldest first.

    Matched two ways because the link is recorded from both ends: the report names the
    visit it was uploaded against, and the visit lists the reports whose analyses were
    folded into its results. A report uploaded without a visit id, then attached when
    the results were reviewed, only satisfies the second.
    """
    attached = set(visit.report_ids)
    matching = [r for r in reports if r.visit_id == visit.id or r.id in attached]
    return sorted(matching, key=lambda r: r.uploaded_at)


def build_soap_note(
    profile: PatientProfile,
    visit: Visit,
    history: Optional[list[Visit]] = None,
    reports: Optional[list[StoredReport]] = None,
    include_assistant_differential: bool = True,
) -> SoapNote:
    """Assemble the note for one visit.

    `history` may be the patient's whole visit list in any order — this visit and
    anything later than it are filtered out, so passing everything the repository
    returned is correct and the caller cannot change the note's meaning by ordering the
    list differently. `reports` is likewise the patient's whole report list; only this
    visit's are used.

    Setting `include_assistant_differential` to False leaves the assistant out of the
    note entirely, for a physician who wants a clean document to hand on.
    """
    # Strictly earlier visits only. A note describes an encounter as it stood on the
    # day, so a diagnosis reached three weeks later has no business in its background —
    # it would read as history the doctor had in front of them at the time.
    others = [
        v for v in (history or []) if v.id != visit.id and v.created_at < visit.created_at
    ]
    visit_reports = reports_for_visit(visit, reports or [])

    # "The record holds no earlier visit" is a real clinical statement and is worth
    # making. "Nobody handed us the history" is not the same thing, and renders as
    # "Not recorded" instead — which is why this depends on `history is None` rather
    # than on the list being empty.
    previous = _previous_diagnoses(others)
    if history is not None and not previous:
        previous = [
            f"{len(others)} earlier visit(s) on record, none with a working diagnosis."
            if others
            else "None on record before this visit."
        ]

    return SoapNote(
        patient_id=profile.id,
        patient_name=profile.full_name,
        patient_summary=_demographics(profile),
        visit_id=visit.id,
        visit_date=visit.created_at.date(),
        visit_status=visit.status.value,
        subjective=SubjectiveSection(
            chief_complaint=visit.chief_complaint,
            symptoms=list(visit.symptoms),
            allergies=_allergies(profile),
            current_medications=_medications(profile),
            past_medical_history=_conditions(profile),
            social_history=_smoking(profile),
            previous_diagnoses=previous,
            previous_visit_count=len(others),
            background_notes=profile.notes,
        ),
        objective=ObjectiveSection(
            vitals=_vitals(visit),
            physical_exam=visit.physical_exam,
            observations=visit.observations,
            investigation_results=visit.results_summary,
            reports=[_report_finding(r) for r in visit_reports],
        ),
        assessment=AssessmentSection(
            working_diagnosis=(
                visit.working_diagnosis.label if visit.working_diagnosis else None
            ),
            icd10_code=(
                visit.working_diagnosis.icd10_code if visit.working_diagnosis else None
            ),
            reasoning=(
                visit.working_diagnosis.reasoning if visit.working_diagnosis else None
            ),
            assistant_differential=(
                _differential(visit) if include_assistant_differential else []
            ),
        ),
        plan=PlanSection(
            investigations=_investigations(visit),
            medications=_prescriptions(visit),
            notes=visit.doctor_notes,
            visit_status=visit.status.value,
        ),
    )
