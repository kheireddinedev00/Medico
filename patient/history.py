"""Assembles the clinical context that gets injected into every AI request.

This module is the concrete form of the project's central design principle: the
assistant is stateless, and the record is the source of truth. Before each request the
backend calls `build_clinical_context(...)`, renders it with `to_prompt_text()`, and
puts the result in the prompt. The model is told what the patient's chart says, every
single time, and remembers nothing between calls.

Two properties are deliberate:

1. `build_clinical_context` is a pure function over a profile and a list of visits.
   It touches no database and makes no network call. That is what lets your teammate's
   backend supply the same two arguments from a real API later without a single change
   to the clinical layer — and it is what makes this testable with fixture data today.

2. No LLM is involved in building the summary. A model summarising the chart before
   another model reasons over it would be a second place for a drug allergy to get
   lost. The rendering here is deterministic string formatting; what goes in comes out.

Absent data renders as "None recorded", never as a negative finding. "No allergies
recorded" and "allergies were never asked about" are the same string in a database and
must not be silently presented to a clinician as if they were "no allergies".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from clinical.consultation_state import OPEN_STATUSES
from config import CONTEXT_RECENT_REPORTS, CONTEXT_RECENT_VISITS
from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Visit

_NONE = "None recorded."
_NOT_ANALYSED = "Uploaded but not yet analysed — the physician has not run the analysis."


class ClinicalContext(BaseModel):
    """The chart projection handed to the assistant for one request.

    Keeps the typed objects rather than only the rendered string so a future HTTP
    layer can return this as JSON, and so tests can assert on structure.
    """

    model_config = ConfigDict(extra="forbid")

    profile: PatientProfile
    recent_visits: list[Visit] = []
    total_visits: int = 0
    recent_reports: list[StoredReport] = []
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_prompt_text(self) -> str:
        return _render(self)


def build_clinical_context(
    profile: PatientProfile,
    visits: list[Visit],
    reports: Optional[list[StoredReport]] = None,
    recent_visits: int = CONTEXT_RECENT_VISITS,
) -> ClinicalContext:
    """Project a chart into the context the assistant sees.

    `visits` may be in any order; they are sorted newest-first here so callers cannot
    change the meaning of "recent" by handing over a differently ordered list. The same
    applies to `reports`.
    """
    ordered = sorted(visits, key=lambda v: v.created_at, reverse=True)
    ordered_reports = sorted(
        reports or [], key=lambda r: r.uploaded_at, reverse=True
    )
    return ClinicalContext(
        profile=profile,
        recent_visits=ordered[:recent_visits],
        total_visits=len(ordered),
        recent_reports=ordered_reports[:CONTEXT_RECENT_REPORTS],
    )


# --------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------


def _demographics(profile: PatientProfile) -> str:
    """Render age and sex, keeping a missing one clearly separate from a present one.

    Age and sex both drive real thresholds, so a chart missing either has to say so in
    words the model cannot misread as a value.
    """
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
        parts = [med.name]
        if med.dose:
            parts.append(med.dose)
        if med.frequency:
            parts.append(med.frequency)
        line = " ".join(parts)
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


def _smoking(profile: PatientProfile) -> str:
    smoking = profile.smoking
    if smoking.status == "unknown":
        return "Not recorded."
    if smoking.status == "never":
        return "Never smoker."
    parts = [f"{smoking.status} smoker"]
    if smoking.pack_years is not None:
        parts.append(f"{smoking.pack_years:g} pack-years")
    if smoking.quit_year is not None:
        parts.append(f"quit {smoking.quit_year}")
    return ", ".join(parts) + "."


def _previous_diagnoses(visits: list[Visit]) -> list[str]:
    """Every previous working diagnosis, not only the respiratory ones.

    Filtering to respiratory diagnoses would hide exactly the comorbidities that
    change the differential — heart failure and anaemia both present as breathlessness.
    The assistant is better served by the whole list.
    """
    lines = []
    for visit in visits:
        diagnosis = visit.working_diagnosis
        if diagnosis is None:
            continue
        code = f" [{diagnosis.icd10_code}]" if diagnosis.icd10_code else ""
        lines.append(f"{_day(visit.created_at)} — {diagnosis.label}{code}")
    return lines


def _vitals_line(visit: Visit) -> Optional[str]:
    v = visit.vitals
    parts = []
    if v.temperature_c is not None:
        parts.append(f"T {v.temperature_c}C")
    if v.heart_rate is not None:
        parts.append(f"HR {v.heart_rate}")
    if v.respiratory_rate is not None:
        parts.append(f"RR {v.respiratory_rate}")
    if v.blood_pressure:
        parts.append(f"BP {v.blood_pressure}")
    if v.spo2 is not None:
        parts.append(f"SpO2 {v.spo2:g}%")
    if v.weight_kg is not None:
        parts.append(f"Weight {v.weight_kg:g} kg")
    return ", ".join(parts) if parts else None


def _visit_block(visit: Visit, index: int) -> list[str]:
    lines = [f"  [{index}] {_day(visit.created_at)} — status {visit.status.value}"]

    def field(label: str, value: Optional[str]) -> None:
        if value:
            lines.append(f"      {label}: {value}")

    field("Chief complaint", visit.chief_complaint)
    field("Symptoms", "; ".join(visit.symptoms) if visit.symptoms else None)
    field("Vitals", _vitals_line(visit))
    field("Examination", visit.physical_exam)
    field("Observations", visit.observations)

    if visit.working_diagnosis:
        code = (
            f" [{visit.working_diagnosis.icd10_code}]"
            if visit.working_diagnosis.icd10_code
            else ""
        )
        field("Working diagnosis", f"{visit.working_diagnosis.label}{code}")

    if visit.ordered_investigations:
        ordered = "; ".join(
            f"{i.name} ({i.category}, {i.status})" for i in visit.ordered_investigations
        )
        field("Investigations ordered", ordered)

    if visit.prescribed_medications:
        prescribed = "; ".join(
            " ".join(p for p in [m.name, m.dose, m.frequency, m.duration] if p)
            for m in visit.prescribed_medications
        )
        field("Prescribed", prescribed)

    field("Doctor notes", visit.doctor_notes)
    return lines


def _day(moment: datetime) -> date:
    return moment.date()


def format_findings(visit: Visit) -> str:
    """Render the consultation happening right now, for injection alongside the context.

    Only what the physician observed goes in: complaint, symptoms, vitals, examination,
    observations. Any working diagnosis already on the draft visit is deliberately left
    out — handing the assistant the doctor's leading hypothesis invites it to agree with
    the doctor instead of producing an independent differential.
    """
    lines = ["=== TODAY'S CONSULTATION ==="]

    def field(label: str, value: Optional[str]) -> None:
        if value:
            lines.append(f"{label}: {value}")

    field("Chief complaint", visit.chief_complaint)
    field("Symptoms", "; ".join(visit.symptoms) if visit.symptoms else None)
    field("Vitals", _vitals_line(visit))
    field("Examination", visit.physical_exam)
    field("Additional observations", visit.observations)

    if len(lines) == 1:
        lines.append("No findings have been recorded for this consultation yet.")
    lines.append("=== END OF TODAY'S CONSULTATION ===")

    # Results get their own block, after the presentation and clearly labelled. They
    # are the newest and most decisive information available, and burying them inside
    # the examination notes is why an earlier version kept ranking pneumonia first
    # after a clear chest X-ray.
    if visit.results_summary:
        lines += [
            "",
            "=== INVESTIGATION RESULTS (newest information — weigh these above the "
            "initial presentation) ===",
            visit.results_summary,
            "=== END OF INVESTIGATION RESULTS ===",
        ]

    return "\n".join(lines)


def _section(title: str, lines: list[str]) -> list[str]:
    if not lines:
        return [f"{title}: {_NONE}"]
    return [f"{title}:"] + [f"  - {line}" for line in lines]


def _render(context: ClinicalContext) -> str:
    profile = context.profile
    out: list[str] = [
        "=== PATIENT CLINICAL CONTEXT ===",
        "Prepared from the medical record by the system. An item shown as 'None "
        "recorded' is absent from the chart, which is NOT the same as ruled out.",
        "",
        f"PATIENT: {_demographics(profile)} (record id {profile.id})",
    ]

    out += _section("ALLERGIES", _allergies(profile))
    out += _section("CURRENT MEDICATIONS", _medications(profile))
    out += _section("CHRONIC CONDITIONS", _conditions(profile))
    out.append(f"SMOKING: {_smoking(profile)}")
    out += _section("PREVIOUS DIAGNOSES", _previous_diagnoses(context.recent_visits))

    if profile.notes:
        out.append(f"BACKGROUND NOTES: {profile.notes}")

    out.append("")
    if not context.recent_visits:
        out.append("PREVIOUS VISITS: None. This is the patient's first recorded visit.")
    else:
        shown = len(context.recent_visits)
        header = f"PREVIOUS VISITS (showing {shown} of {context.total_visits}, newest first):"
        out.append(header)
        for i, visit in enumerate(context.recent_visits, 1):
            out += _visit_block(visit, i)

    open_visits = [v for v in context.recent_visits if v.status in OPEN_STATUSES]
    if open_visits:
        out.append("")
        out.append("UNFINISHED ENCOUNTERS:")
        for visit in open_visits:
            out.append(
                f"  - visit from {_day(visit.created_at)} is still at "
                f"{visit.status.value}"
            )

    out.append("")
    out += _report_section("RECENT LABORATORY FINDINGS", context.recent_reports, "laboratory")
    out += _report_section("RECENT IMAGING FINDINGS", context.recent_reports, "radiology")
    out.append("=== END OF CLINICAL CONTEXT ===")
    return "\n".join(out)


def _report_section(title: str, reports: list[StoredReport], kind: str) -> list[str]:
    """Render the analysed reports of one kind.

    A report that was uploaded but never analysed is listed as exactly that, rather
    than omitted. "No imaging on file" and "the chest film is sitting there unread"
    are different situations, and only one of them is reassuring.
    """
    matching = [r for r in reports if r.kind == kind]
    if not matching:
        return [f"{title}: {_NONE}"]

    lines = [f"{title}:"]
    for report in matching:
        lines.append(f"  {report.label()}")
        if report.analysis is None:
            lines.append(f"    {_NOT_ANALYSED}")
            continue
        if report.analysis.summary:
            lines.append(f"    {report.analysis.summary}")
        for result in report.analysis.abnormal():
            lines.append(f"    - {result.describe()}")
        for pattern in report.analysis.patterns:
            lines.append(f"    - {pattern}")
    return lines
