"""CLI for laboratory and radiology reports.

extract a report to JSON without storing it (the original behaviour):
venv\\Scripts\\python -m report_reader.cli extract "data/sample_reports/33806e7015fbfcaff211.png"

upload a report into a patient's record, optionally against the visit that ordered it:
venv\\Scripts\\python -m report_reader.cli upload <path> --patient P-001 --visit V-002

analyse an uploaded report — a separate, explicit action:
venv\\Scripts\\python -m report_reader.cli analyze <report-id>

list a patient's reports:
venv\\Scripts\\python -m report_reader.cli list --patient P-001

Upload and analyse are deliberately two commands. Uploading transcribes the page and
nothing more; analysing produces an interpretation and is something the physician
chooses to do. Keeping them apart is what stops interpretation leaking into the stored
transcription, where it would be indistinguishable from what was printed on the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from patient.report import StoredReport
from report_reader.analyzer import ReportAnalysis, analyse
from report_reader.extractor import ExtractionError, extract_report
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.report_repository import SqliteReportRepository
from storage.visit_repository import SqliteVisitRepository

RADIOLOGY_HINTS = ("x-ray", "xray", "radiograph", "ct ", "hrct", "ultrasound", "mri", "imaging")


def guess_kind(report_type: str | None) -> str:
    """Laboratory unless the printed report type says otherwise."""
    lowered = (report_type or "").lower()
    return "radiology" if any(hint in lowered for hint in RADIOLOGY_HINTS) else "laboratory"


def print_analysis(analysis: ReportAnalysis) -> None:
    print("\n" + "=" * 70)
    print("REPORT ANALYSIS — describes this report only, not the patient")
    print("=" * 70)
    if analysis.summary:
        print(f"\n{analysis.summary}")

    abnormal = analysis.abnormal()
    if abnormal:
        print("\nOUT OF RANGE")
        for result in abnormal:
            print(f"  - {result.describe()}")
    else:
        print("\nNo values fell outside their reference ranges.")

    if analysis.patterns:
        print("\nPATTERNS")
        for pattern in analysis.patterns:
            print(f"  - {pattern}")
    if analysis.caveats:
        print("\nCAVEATS")
        for caveat in analysis.caveats:
            print(f"  - {caveat}")

    print("\n" + "-" * 70)
    print("Descriptive only. What this means for the patient is the physician's "
          "judgement,\nand is taken up by the assistant when the visit is resumed.")


# --------------------------------------------------------------------------------


def cmd_extract(args) -> int:
    try:
        result = extract_report(args.path)
    except (FileNotFoundError, ExtractionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = json.dumps(result, indent=2, ensure_ascii=False)
    print(output)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"\nSaved to {args.out}", file=sys.stderr)
    return 0


def cmd_upload(args) -> int:
    conn = db.connect()
    try:
        if SqlitePatientRepository(conn).get(args.patient) is None:
            print(f"No patient with id {args.patient}", file=sys.stderr)
            return 1
        if args.visit and SqliteVisitRepository(conn).get(args.visit) is None:
            print(f"No visit with id {args.visit}", file=sys.stderr)
            return 1

        try:
            extracted = extract_report(args.path)
        except (FileNotFoundError, ExtractionError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        document = extracted.get("document") or {}
        report = StoredReport(
            patient_id=args.patient,
            visit_id=args.visit,
            kind=args.kind or guess_kind(document.get("report_type")),
            report_type=document.get("report_type"),
            report_date=document.get("report_date") or document.get("collection_date"),
            source_file=str(Path(args.path).resolve()),
            extracted=extracted,
        )
        SqliteReportRepository(conn).save(report)
    finally:
        conn.close()

    print(f"Stored {report.id} — {report.label()} ({report.kind})")

    # The name printed on the report is not how we identify the patient; the physician
    # chose that. A mismatch is worth a word, not a refusal.
    printed = (extracted.get("patient") or {}).get("name")
    if printed:
        print(f"Name printed on the report: {printed}")

    print(f"\nNothing has been interpreted yet. To analyse it:\n"
          f"  python -m report_reader.cli analyze {report.id}")
    return 0


def cmd_analyze(args) -> int:
    conn = db.connect()
    try:
        repo = SqliteReportRepository(conn)
        report = repo.get(args.report_id)
        if report is None:
            print(f"No report with id {args.report_id}", file=sys.stderr)
            return 1
        if report.is_analysed and not args.again:
            print(f"{report.id} was already analysed on "
                  f"{report.analysed_at.date() if report.analysed_at else 'an earlier date'}.")
            print_analysis(report.analysis)
            print("\nRe-run with --again to analyse it afresh.")
            return 0

        print(f"Analysing {report.id} — {report.label()}...")
        analysis = analyse(report.extracted)
        report.attach_analysis(analysis)
        repo.save(report)
    finally:
        conn.close()

    print_analysis(analysis)
    print(f"\nSaved to the record. It will be offered when visit "
          f"{report.visit_id or '(unlinked)'} is resumed.")
    return 0


def cmd_list(args) -> int:
    conn = db.connect()
    try:
        reports = SqliteReportRepository(conn).list_for_patient(args.patient)
    finally:
        conn.close()

    if not reports:
        print("No reports on file for this patient.")
        return 0
    print(f"\n{len(reports)} report(s), newest first:")
    for report in reports:
        state = "analysed" if report.is_analysed else "NOT analysed"
        visit = report.visit_id or "-"
        print(f"  {report.id:<16} {report.uploaded_at.date()}  {report.kind:<11} "
              f"{state:<12} visit {visit}  {report.label()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Medical report extraction and analysis.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="Extract JSON and print it, without storing.")
    p.add_argument("path", help="Path to a report image (PNG/JPG/...) or PDF.")
    p.add_argument("--out", help="Also write the JSON to this file.")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("upload", help="Extract and store a report in a patient's record.")
    p.add_argument("path")
    p.add_argument("--patient", required=True, help="Patient record id, e.g. P-001.")
    p.add_argument("--visit", help="Visit that ordered it, e.g. V-002.")
    p.add_argument("--kind", choices=["laboratory", "radiology", "other"],
                   help="Override the detected report kind.")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("analyze", help="Analyse a stored report (explicit action).")
    p.add_argument("report_id")
    p.add_argument("--again", action="store_true", help="Re-analyse an already analysed report.")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("list", help="List a patient's reports.")
    p.add_argument("--patient", required=True)
    p.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
