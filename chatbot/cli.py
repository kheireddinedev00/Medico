"""Clinical decision support assistant — CLI.

start a new consultation:
venv\\Scripts\\python -m chatbot.cli --patient P-001

re-run a saved presentation without retyping it:
venv\\Scripts\\python -m chatbot.cli --patient P-001 --findings data/seed/findings_example.json

continue an unfinished visit (e.g. one waiting for test results):
venv\\Scripts\\python -m chatbot.cli --resume V-002

list a patient's visits:
venv\\Scripts\\python -m chatbot.cli --patient P-001 --visits

The interface is a loop over the consultation's state: each state has one step, and the
step ends by moving the visit somewhere new. Nothing is written to the record until a
diagnosis is chosen, and everything after that is saved as it happens.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

from clinical.consultation_state import VisitStatus
from clinical.differential import Assessment, AssessmentError
from clinical.icd10 import load_code_list
from clinical.session import ConsultationError, ConsultationSession
from patient.visit import Investigation, PrescribedMedication, Visit, Vitals
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.report_repository import SqliteReportRepository
from storage.visit_repository import SqliteVisitRepository

DISCLAIMER = (
    "Decision support only. Every suggestion requires physician review; the assistant "
    "does not diagnose, and does not see the patient."
)

CATEGORIES = {"l": "laboratory", "r": "radiology", "o": "other"}


# --------------------------------------------------------------------------------
# Small input helpers
# --------------------------------------------------------------------------------


def _ask(label: str) -> str | None:
    value = input(f"  {label}: ").strip()
    return value or None


def _ask_number(label: str, cast=float):
    raw = _ask(label)
    if raw is None:
        return None
    try:
        return cast(raw)
    except ValueError:
        print(f"    (not a number — skipping {label})")
        return None


def _confirm(question: str) -> bool:
    while True:
        answer = input(f"\n{question} [y/n]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False


# --------------------------------------------------------------------------------
# Collecting the consultation
# --------------------------------------------------------------------------------


def collect_findings(patient_id: str) -> Visit:
    print("\nEnter today's findings (press Enter to skip any field).")
    complaint = _ask("Chief complaint")
    symptoms_raw = _ask("Symptoms (separate with commas)")
    symptoms = [s.strip() for s in symptoms_raw.split(",")] if symptoms_raw else []

    print("  -- vital signs --")
    vitals = Vitals(
        temperature_c=_ask_number("Temperature (C)"),
        heart_rate=_ask_number("Heart rate", int),
        respiratory_rate=_ask_number("Respiratory rate", int),
        blood_pressure=_ask("Blood pressure (e.g. 128/76)"),
        spo2=_ask_number("SpO2 (%)"),
    )

    return Visit(
        patient_id=patient_id,
        chief_complaint=complaint,
        symptoms=symptoms,
        vitals=vitals,
        physical_exam=_ask("Physical examination"),
        observations=_ask("Additional observations"),
    )


def load_findings(path: Path, patient_id: str) -> Visit:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("_note", None)
    data["patient_id"] = patient_id
    return Visit.model_validate(data)


def _pick_numbers(count: int) -> list[int]:
    """Read a selection like '1,3' and return zero-based indices."""
    while True:
        raw = input(f"  Select by number (e.g. 1,3), or Enter to skip: ").strip()
        if not raw:
            return []
        try:
            chosen = [int(part) for part in raw.replace(" ", "").split(",") if part]
        except ValueError:
            print("    Numbers separated by commas, please.")
            continue
        bad = [n for n in chosen if not 1 <= n <= count]
        if bad:
            print(f"    No option {bad[0]}. Pick 1-{count}.")
            continue
        return [n - 1 for n in chosen]


def collect_investigations(suggested: list | None = None) -> list[Investigation]:
    ordered: list[Investigation] = []

    if suggested:
        print("\nThe assistant suggests:")
        for i, item in enumerate(suggested, 1):
            print(f"  {i}. {item.name}")
            if item.rationale:
                print(f"     {item.rationale}")
            if item.changes_management:
                print(f"     Changes management: {item.changes_management}")
        ordered = [suggested[i].to_investigation() for i in _pick_numbers(len(suggested))]

    print("\nAdd any others (blank name to finish).")
    while True:
        name = _ask("Investigation")
        if name is None:
            return ordered
        raw = input("    Category [l]ab / [r]adiology / [o]ther: ").strip().lower()
        ordered.append(Investigation(name=name, category=CATEGORIES.get(raw[:1], "other")))


def collect_medications(advice=None) -> list[PrescribedMedication]:
    prescribed: list[PrescribedMedication] = []

    if advice is not None:
        print_medication_advice(advice)
        if advice.medications:
            prescribed = [
                advice.medications[i].to_prescription()
                for i in _pick_numbers(len(advice.medications))
            ]

    print("\nAdd any others (blank name to finish).")
    while True:
        name = _ask("Medication")
        if name is None:
            return prescribed
        prescribed.append(
            PrescribedMedication(
                name=name,
                dose=_ask("  Dose"),
                frequency=_ask("  Frequency"),
                duration=_ask("  Duration"),
            )
        )


def choose_diagnosis(assessment: Assessment) -> tuple[str, str | None] | None:
    """Let the doctor pick from the differential, or enter their own.

    A bare number always means "the nth suggestion" — never a diagnosis label. An
    out-of-range number is re-asked rather than accepted as free text, which is how a
    visit once ended up with a working diagnosis of "9".
    """
    count = len(assessment.differential)
    print("\nSelect the working diagnosis.")
    print(f"  Enter a number (1-{count}), type your own, or 'q' to stop here.")

    while True:
        raw = input("  Working diagnosis: ").strip()
        if not raw or raw.lower() == "q":
            return None

        if raw.isdigit():
            choice = int(raw)
            if not 1 <= choice <= count:
                print(f"    There is no option {choice}. Pick 1-{count}, or type a name.")
                continue
            label = assessment.differential[choice - 1].label
            print(f"  → {label}")
        else:
            label = raw

        return label, _ask("ICD-10 code (optional)")


# --------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------


def _print_list(title: str, items: list[str]) -> None:
    if not items:
        return
    print(f"\n{title}")
    for item in items:
        print(f"  - {item}")


def print_assessment(assessment: Assessment) -> None:
    print("\n" + "=" * 70)
    print("ASSESSMENT — suggestions for the physician's consideration")
    print("=" * 70)

    if not assessment.differential:
        print("\nThe assistant returned no differential.")
    else:
        print("\nDIFFERENTIAL (most likely first)")
        for i, diagnosis in enumerate(assessment.differential, 1):
            likelihood = f" — {diagnosis.likelihood}" if diagnosis.likelihood else ""
            print(f"\n  {i}. {diagnosis.label}{likelihood}")
            if diagnosis.reasoning:
                print(f"     {diagnosis.reasoning}")
            cited = assessment.cited_sources(diagnosis)
            if cited:
                print(f"     Evidence: {', '.join(f'[{s.index}]' for s in cited)}")

    _print_list("CONCERNING FEATURES", assessment.concerning_features)
    _print_list("MISSING INFORMATION THE ASSISTANT WOULD WANT", assessment.missing_information)
    _print_list("WHAT IN THE RECORD SHAPED THIS", assessment.context_factors)

    if assessment.sources:
        print("\nEVIDENCE SUPPLIED TO THE ASSISTANT")
        for source in assessment.sources:
            print(f"  [{source.index}] {source.reference()}")

    print("\n" + "-" * 70)
    print(DISCLAIMER)


def print_medication_advice(advice) -> None:
    """Withheld drugs are printed first, and printed even though nobody can pick them.

    A suggestion that was removed for an allergy is information the physician needs —
    silently omitting it looks identical to the assistant never having considered it.
    """
    if advice.withheld:
        print("\nWITHHELD — NOT OFFERED")
        for warning in advice.withheld:
            print(f"  ✗ {warning.medication}: {warning.reason}")

    if not advice.medications:
        print("\nThe assistant suggested no medications.")
    else:
        print("\nSUGGESTED TREATMENT")
        for i, medication in enumerate(advice.medications, 1):
            details = " ".join(
                p for p in [medication.dose, medication.frequency, medication.duration] if p
            )
            print(f"  {i}. {medication.name} {details}".rstrip())
            if medication.rationale:
                print(f"     {medication.rationale}")
            for warning in advice.cautions_for(medication):
                print(f"     ⚠ CAUTION: {warning.reason}")

    _print_list("NON-DRUG MANAGEMENT", advice.non_drug_advice)
    _print_list("NOTES", advice.notes)


def print_code_advice(advice, code_list=None) -> None:
    """Print suggested codes, stating honestly how far they have been checked."""
    codes = advice.codes
    if code_list is not None:
        header = (f"SUGGESTED ICD-10 CODES (validated against {code_list.provenance}, "
                  f"{len(code_list)} codes — confirm the choice is clinically right)")
    else:
        header = ("SUGGESTED ICD-10 CODES (format checked only — NOT verified against an "
                  "official ICD-10 list; confirm each one before use)")

    if not codes:
        print("\nThe assistant suggested no usable ICD-10 codes.")
        _print_list("NOTES", advice.notes)
        return

    print(f"\n{header}")
    for i, code in enumerate(codes, 1):
        print(f"  {i}. {code.code}  {code.description or ''}".rstrip())
        if code.rationale:
            print(f"     {code.rationale}")
    _print_list("NOTES", advice.notes)


def print_visit_summary(visit: Visit) -> None:
    print("\n" + "-" * 70)
    print(f"Visit {visit.id} — {visit.status.value}")
    if visit.working_diagnosis:
        code = f" [{visit.working_diagnosis.icd10_code}]" if visit.working_diagnosis.icd10_code else ""
        print(f"  Working diagnosis: {visit.working_diagnosis.label}{code}")
    if visit.differential:
        suggested = ", ".join(d.label for d in visit.differential[:3])
        print(f"  Assistant suggested: {suggested}")
    for investigation in visit.ordered_investigations:
        print(f"  Ordered: {investigation.name} ({investigation.category}, {investigation.status})")
    for medication in visit.prescribed_medications:
        details = " ".join(p for p in [medication.dose, medication.frequency, medication.duration] if p)
        print(f"  Prescribed: {medication.name} {details}".rstrip())
    if visit.doctor_notes:
        print(f"  Notes: {visit.doctor_notes}")


# --------------------------------------------------------------------------------
# One step per state
# --------------------------------------------------------------------------------


def _step_initial_assessment(session: ConsultationSession, args) -> bool:
    findings = (
        load_findings(Path(args.findings), session.visit.patient_id)
        if args.findings
        else collect_findings(session.visit.patient_id)
    )
    session.apply_findings(findings)

    print("\nAssessing...")
    assessment = session.assess()
    print_assessment(assessment)

    chosen = choose_diagnosis(assessment)
    if chosen is None:
        print("\nNo diagnosis selected — nothing was saved.")
        return False

    label, code = chosen
    session.select_diagnosis(label, icd10_code=code)
    print(f"\nVisit {session.visit.id} created.")

    if not code and _confirm("Ask the assistant for ICD-10 code suggestions?"):
        advice = session.suggest_codes()
        print_code_advice(advice, load_code_list())
        if advice.codes:
            picked = _pick_numbers(len(advice.codes))
            if picked:
                session.set_icd10_code(advice.codes[picked[0]].code)
                print(f"  → coded {session.visit.working_diagnosis.icd10_code}")

    session.add_note(_ask("Notes (optional)") or "")
    return True


def _step_choose_path(session: ConsultationSession, _args) -> bool:
    if _confirm("Do you want investigations before treating?"):
        session.choose_investigation_path()
    else:
        session.skip_investigations()
    return True


def _step_order_tests(session: ConsultationSession, _args) -> bool:
    suggested = None
    if _confirm("Ask the assistant which investigations would help?"):
        print("\nThinking...")
        suggested = session.suggest_investigations().investigations

    ordered = collect_investigations(suggested)
    if not ordered:
        if _confirm("No investigations entered. Go straight to treatment instead?"):
            session.skip_investigations()
            return True
        print("\nNothing ordered — leaving the visit where it is.")
        return False

    session.order_investigations(ordered)
    session.add_note(_ask("Notes (optional)") or "")
    print("\nThe patient can leave. This visit stays open until the results are reviewed.")
    print_visit_summary(session.visit)
    return False


def _step_waiting_for_tests(session: ConsultationSession, _args) -> bool:
    outstanding = [i for i in session.visit.ordered_investigations if i.status == "ordered"]
    print(f"\nThis visit is waiting for: "
          f"{', '.join(i.name for i in outstanding) or 'unspecified tests'}")

    uploaded = session.pending_reports()
    analysed = [r for r in uploaded if r.is_analysed]
    unanalysed = [r for r in uploaded if not r.is_analysed]

    if unanalysed:
        print("\nUploaded but NOT yet analysed — these will be ignored until you analyse them:")
        for report in unanalysed:
            print(f"  - {report.id}  {report.label()}")
            print(f"    python -m report_reader.cli analyze {report.id}")

    if analysed:
        print("\nAnalysed reports attached to this visit:")
        for report in analysed:
            print(f"  - {report.label()}")
            print(f"    {report.analysis.summary}")

    if not _confirm("Have the results come back?"):
        print("\nLeaving the visit open.")
        return False

    # Ask about each test rather than assuming everything arrived — a visit that marks
    # a still-pending CT as resulted is a visit nobody chases.
    resulted = [i.name for i in outstanding if _confirm(f"Did {i.name} come back?")] \
        if len(outstanding) > 1 else [i.name for i in outstanding]

    if analysed and _confirm("Use the analysed reports as the results?"):
        session.record_results_from_reports(analysed, resulted=resulted)
        session.add_note(_ask("Anything to add (optional)") or "")
        return True

    summary = _ask("Summarise the results") or "(no summary given)"
    session.record_results(summary, resulted=resulted)
    return True


def _step_review_results(session: ConsultationSession, _args) -> bool:
    print("\nRe-assessing with the results...")
    print_assessment(session.assess())

    if _confirm("Do the results change your working diagnosis?"):
        label = _ask("Revised diagnosis")
        if label:
            session.revise_diagnosis(label, icd10_code=_ask("ICD-10 code (optional)"))

    if _confirm("Do you need further investigations?"):
        session.order_more_investigations()
    else:
        session.proceed_to_treatment()
    return True


def _step_treatment(session: ConsultationSession, _args) -> bool:
    advice = None
    if _confirm("Ask the assistant for treatment suggestions?"):
        print("\nThinking...")
        advice = session.suggest_medications()

    session.prescribe(collect_medications(advice))
    session.add_note(_ask("Notes (optional)") or "")

    if _confirm("Schedule a follow-up?"):
        session.schedule_follow_up(_ask("Follow-up plan"))
    else:
        session.complete()
    return True


def _step_follow_up(session: ConsultationSession, _args) -> bool:
    session.complete()
    return True


STEPS = {
    VisitStatus.INITIAL_ASSESSMENT: _step_initial_assessment,
    VisitStatus.ICD10_SELECTION: _step_choose_path,
    VisitStatus.TEST_SELECTION: _step_order_tests,
    VisitStatus.WAITING_FOR_TESTS: _step_waiting_for_tests,
    VisitStatus.RESULTS_REVIEW: _step_review_results,
    VisitStatus.TREATMENT_SELECTION: _step_treatment,
    VisitStatus.FOLLOW_UP: _step_follow_up,
}


# --------------------------------------------------------------------------------


def choose_patient(profiles) -> str | None:
    print("\nPatients on file:")
    for i, profile in enumerate(profiles, 1):
        age = profile.age if profile.age is not None else "?"
        print(f"  {i}. {profile.id:<8} {profile.full_name:<20} {age} {profile.sex}")
    raw = input("\nSelect a patient (number or id): ").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(profiles):
        return profiles[int(raw) - 1].id
    return raw


def list_visits(patients, visits, patient_id: str) -> int:
    if patients.get(patient_id) is None:
        print(f"No patient with id {patient_id}", file=sys.stderr)
        return 1
    records = visits.list_for_patient(patient_id)
    if not records:
        print("No visits recorded for this patient.")
        return 0
    print(f"\n{len(records)} visit(s), newest first:")
    for visit in records:
        diagnosis = visit.working_diagnosis.label if visit.working_diagnosis else "-"
        print(f"  {visit.id:<16} {visit.created_at.date()}  {visit.status.value:<20} {diagnosis}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Clinical decision support assistant.")
    parser.add_argument("--patient", help="Patient record id, e.g. P-001.")
    parser.add_argument("--resume", help="Continue an unfinished visit by id.")
    parser.add_argument("--findings", help="JSON file with today's findings.")
    parser.add_argument("--visits", action="store_true", help="List the patient's visits and exit.")
    parser.add_argument(
        "--show-context", action="store_true", help="Print the record summary sent to the model."
    )
    args = parser.parse_args()

    conn = db.connect()
    try:
        patients = SqlitePatientRepository(conn)
        visits = SqliteVisitRepository(conn)

        if args.visits:
            if not args.patient:
                print("--visits needs --patient", file=sys.stderr)
                return 1
            return list_visits(patients, visits, args.patient)

        reports = SqliteReportRepository(conn)
        try:
            if args.resume:
                session = ConsultationSession.resume(patients, visits, args.resume, reports)
            else:
                profiles = patients.list_all()
                if not profiles:
                    print("No patients on file. Run: python -m storage.cli seed --reset", file=sys.stderr)
                    return 1
                patient_id = args.patient or choose_patient(profiles)
                session = ConsultationSession.start(patients, visits, patient_id or "", reports)
        except ConsultationError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1

        print(f"\nPatient: {session.context.profile.full_name} ({session.context.profile.id})")
        if args.resume:
            print_visit_summary(session.visit)
        if args.show_context:
            print("\n" + session.context.to_prompt_text())

        try:
            while session.visit.status is not VisitStatus.COMPLETED:
                step = STEPS[session.visit.status]
                if not step(session, args):
                    return 0
        except AssessmentError as exc:
            print(f"\nThe assistant's reply could not be used: {exc}", file=sys.stderr)
            return 1
        except (EOFError, KeyboardInterrupt):
            # Whatever the consultation reached is already saved; only the step in
            # progress is lost.
            print(
                f"\nInterrupted. Visit left at {session.visit.status.value}"
                + (f" — resume with --resume {session.visit.id}" if session.visit.working_diagnosis else " (nothing saved)"),
                file=sys.stderr,
            )
            return 1
        except ConsultationError as exc:
            print(f"\nStopped: {exc}", file=sys.stderr)
            return 1

        print("\nConsultation complete.")
        print_visit_summary(session.visit)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
