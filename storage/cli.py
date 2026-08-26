"""CLI for the local medical record — the testing surface for the data layer.

to load the fixture patients:
venv\\Scripts\\python -m storage.cli seed --reset

then:
venv\\Scripts\\python -m storage.cli patients
venv\\Scripts\\python -m storage.cli show P-001
venv\\Scripts\\python -m storage.cli context P-001

`context` prints the exact text that will be injected into every AI request once the
doctor assistant lands. If it looks wrong here, it will be wrong in the prompt.
"""

from __future__ import annotations

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from patient.history import build_clinical_context
from storage import db, seed as seeder
from storage.patient_repository import SqlitePatientRepository
from storage.report_repository import SqliteReportRepository
from storage.visit_repository import SqliteVisitRepository


def _cmd_seed(args) -> int:
    patients, visits = seeder.seed(reset=args.reset)
    print(f"Loaded {patients} patient(s) and {visits} visit(s) into {db.DB_PATH}")
    return 0


def _cmd_patients(_args) -> int:
    conn = db.connect()
    try:
        profiles = SqlitePatientRepository(conn).list_all()
    finally:
        conn.close()

    if not profiles:
        print("No patients in the record store. Run: python -m storage.cli seed")
        return 0

    for profile in profiles:
        age = profile.age if profile.age is not None else "?"
        print(f"{profile.id:<8} {profile.full_name:<20} {age} {profile.sex}")
    return 0


def _cmd_show(args) -> int:
    conn = db.connect()
    try:
        profile = SqlitePatientRepository(conn).get(args.patient_id)
        if profile is None:
            print(f"No patient with id {args.patient_id}", file=sys.stderr)
            return 1
        visits = SqliteVisitRepository(conn).list_for_patient(args.patient_id)
    finally:
        conn.close()

    print(profile.model_dump_json(indent=2))
    print(f"\n{len(visits)} visit(s):")
    for visit in visits:
        print(f"  {visit.id}  {visit.created_at.date()}  {visit.status.value}")
    return 0


def _cmd_timeline(args) -> int:
    """The patient's whole record in one column, oldest first.

    Visits and reports are interleaved by date rather than listed separately, because
    what a physician wants to see is the order things happened in — a report that
    arrived between two visits is the reason the second one went differently.
    """
    conn = db.connect()
    try:
        profile = SqlitePatientRepository(conn).get(args.patient_id)
        if profile is None:
            print(f"No patient with id {args.patient_id}", file=sys.stderr)
            return 1
        visits = SqliteVisitRepository(conn).list_for_patient(args.patient_id)
        reports = SqliteReportRepository(conn).list_for_patient(args.patient_id)
    finally:
        conn.close()

    age = profile.age if profile.age is not None else "?"
    print(f"\n{profile.full_name} ({profile.id}) — {age} {profile.sex}")
    print("=" * 70)

    events = [(v.created_at, "visit", v) for v in visits]
    events += [(r.uploaded_at, "report", r) for r in reports]
    if not events:
        print("\nNothing recorded yet.")
        return 0

    for when, kind, item in sorted(events, key=lambda e: e[0]):
        if kind == "visit":
            print(f"\n{when.date()}  VISIT {item.id}  [{item.status.value}]")
            if item.chief_complaint:
                print(f"           {item.chief_complaint}")
            if item.working_diagnosis:
                code = f" [{item.working_diagnosis.icd10_code}]" if item.working_diagnosis.icd10_code else ""
                print(f"           Diagnosis: {item.working_diagnosis.label}{code}")
            for investigation in item.ordered_investigations:
                print(f"           Ordered:   {investigation.name} ({investigation.status})")
            for medication in item.prescribed_medications:
                details = " ".join(p for p in [medication.dose, medication.frequency] if p)
                print(f"           Treated:   {medication.name} {details}".rstrip())
        else:
            state = "analysed" if item.is_analysed else "NOT analysed"
            linked = f" for visit {item.visit_id}" if item.visit_id else ""
            print(f"\n{when.date()}  REPORT {item.id}  [{state}]{linked}")
            print(f"           {item.label()}")
            if item.analysis:
                for abnormal in item.analysis.abnormal():
                    print(f"           - {abnormal.describe()}")
    print()
    return 0


def _cmd_context(args) -> int:
    conn = db.connect()
    try:
        profile = SqlitePatientRepository(conn).get(args.patient_id)
        if profile is None:
            print(f"No patient with id {args.patient_id}", file=sys.stderr)
            return 1
        visits = SqliteVisitRepository(conn).list_for_patient(args.patient_id)
        reports = SqliteReportRepository(conn).list_for_patient(args.patient_id)
    finally:
        conn.close()

    print(build_clinical_context(profile, visits, reports).to_prompt_text())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the local medical record.")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_cmd = sub.add_parser("seed", help="Load the fixture patients.")
    seed_cmd.add_argument(
        "--reset", action="store_true", help="Delete all existing records first."
    )
    seed_cmd.set_defaults(func=_cmd_seed)

    sub.add_parser("patients", help="List all patients.").set_defaults(func=_cmd_patients)

    show_cmd = sub.add_parser("show", help="Print one patient's stored record.")
    show_cmd.add_argument("patient_id")
    show_cmd.set_defaults(func=_cmd_show)

    timeline_cmd = sub.add_parser(
        "timeline", help="Print the patient's visits and reports in date order."
    )
    timeline_cmd.add_argument("patient_id")
    timeline_cmd.set_defaults(func=_cmd_timeline)

    ctx_cmd = sub.add_parser(
        "context", help="Print the clinical context block the assistant will receive."
    )
    ctx_cmd.add_argument("patient_id")
    ctx_cmd.set_defaults(func=_cmd_context)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
