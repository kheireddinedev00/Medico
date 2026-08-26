"""CLI for SOAP notes.

the note for one visit:
venv\\Scripts\\python -m soap.cli V-002

the latest visit's note, when you know the patient and not the visit:
venv\\Scripts\\python -m soap.cli --patient P-001

every visit the patient has had, oldest first:
venv\\Scripts\\python -m soap.cli --patient P-001 --all

the visits and their ids, to pick one:
venv\\Scripts\\python -m soap.cli --patient P-001 --list

as Markdown, written to a file:
venv\\Scripts\\python -m soap.cli V-002 --format markdown --out note.md

without the assistant's differential, for a note to hand on:
venv\\Scripts\\python -m soap.cli V-002 --no-ai

Read-only. Generating a note never writes to the record, so it can be run on a visit
at any stage — including one still in progress — without changing anything. The note is
rebuilt from the chart each time rather than stored, so it cannot fall out of date with
the record it describes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from patient.profile import PatientProfile
from patient.visit import Visit
from soap.note import SoapNote, build_soap_note
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.report_repository import SqliteReportRepository
from storage.visit_repository import SqliteVisitRepository


class NotFound(RuntimeError):
    """The patient or visit the physician asked for is not in the record."""


def render(note: SoapNote, fmt: str) -> str:
    if fmt == "json":
        return note.model_dump_json(indent=2)
    if fmt == "markdown":
        return note.to_markdown()
    return note.to_text()


def _load(conn, visit_id: str | None, patient_id: str | None, want_all: bool):
    """Resolve the request into a profile and the visits to write notes for."""
    patients = SqlitePatientRepository(conn)
    visits = SqliteVisitRepository(conn)

    if visit_id:
        visit = visits.get(visit_id)
        if visit is None:
            raise NotFound(f"No visit with id {visit_id}")
        profile = patients.get(visit.patient_id)
        if profile is None:
            raise NotFound(f"Visit {visit_id} points at missing patient {visit.patient_id}")
        return profile, [visit], visits.list_for_patient(visit.patient_id)

    profile = patients.get(patient_id)
    if profile is None:
        raise NotFound(f"No patient with id {patient_id}")
    history = visits.list_for_patient(patient_id)
    if not history:
        raise NotFound(f"{profile.full_name} ({patient_id}) has no recorded visits.")

    # list_for_patient is newest first; --all reads better oldest first, as a story.
    selected = list(reversed(history)) if want_all else [history[0]]
    return profile, selected, history


def cmd_note(args) -> int:
    if not args.visit_id and not args.patient:
        print(
            "Give a visit id, or --patient to use that patient's latest visit.",
            file=sys.stderr,
        )
        return 2

    conn = db.connect()
    try:
        try:
            profile, selected, history = _load(
                conn, args.visit_id, args.patient, args.all
            )
        except NotFound as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        reports = SqliteReportRepository(conn).list_for_patient(profile.id)
    finally:
        conn.close()

    output = _render_all(profile, selected, history, reports, args)
    print(output)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        # stderr so `--out` can be combined with a pipe without corrupting the note.
        print(f"\nWritten to {args.out}", file=sys.stderr)
    return 0


def _render_all(
    profile: PatientProfile,
    selected: list[Visit],
    history: list[Visit],
    reports,
    args,
) -> str:
    """Render one note, or several joined by a separator the chosen format understands."""
    notes = [
        render(
            build_soap_note(
                profile,
                visit,
                history=history,
                reports=reports,
                include_assistant_differential=not args.no_ai,
            ),
            args.format,
        )
        for visit in selected
    ]
    if len(notes) == 1:
        return notes[0]
    # A JSON array stays parseable; the readable formats get a visible break. Each
    # note already opens with its own banner, so blank lines are enough of a divider.
    if args.format == "json":
        return "[\n" + ",\n".join(notes) + "\n]"
    return "\n\n\n".join(notes)


def cmd_list(args) -> int:
    conn = db.connect()
    try:
        profile = SqlitePatientRepository(conn).get(args.patient)
        if profile is None:
            print(f"No patient with id {args.patient}", file=sys.stderr)
            return 1
        visits = SqliteVisitRepository(conn).list_for_patient(args.patient)
    finally:
        conn.close()

    if not visits:
        print(f"{profile.full_name} ({profile.id}) has no recorded visits.")
        return 0

    print(f"\n{profile.full_name} ({profile.id}) — {len(visits)} visit(s), newest first:")
    for visit in visits:
        diagnosis = (
            visit.working_diagnosis.label if visit.working_diagnosis else "no diagnosis yet"
        )
        print(f"  {visit.id:<16} {visit.created_at.date()}  {visit.status.value:<20} {diagnosis}")
    print(f"\nA note for any of them:\n  python -m soap.cli <visit-id>")
    return 0


def main() -> int:
    # No subcommands here, unlike the other CLIs in the project. This tool does one
    # thing, and `soap.cli V-002` is what a doctor will type; making them type
    # `soap.cli note V-002` to get it would be ceremony for its own sake.
    parser = argparse.ArgumentParser(
        description="Generate a SOAP note from the medical record.",
    )
    parser.add_argument("visit_id", nargs="?", help="Visit to write the note for, e.g. V-002.")
    parser.add_argument("--patient", help="Use this patient's latest visit instead.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="With --patient: a note for every visit, oldest first.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="With --patient: list that patient's visits and their ids, and stop.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument("--out", help="Also write the note to this file.")
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Leave the assistant's suggested differential out of the note.",
    )

    args = parser.parse_args()
    if args.list:
        if not args.patient:
            print("--list needs --patient.", file=sys.stderr)
            return 2
        return cmd_list(args)
    return cmd_note(args)


if __name__ == "__main__":
    raise SystemExit(main())
