"""Turns a `SoapNote` into something a physician reads.

Plain text for the terminal, Markdown for anything that gets pasted elsewhere. Both are
driven by the same `_entries` functions, so a field cannot appear in one format and
quietly go missing from the other.

The rendering rules that matter clinically:

- A field with no value prints as "Not recorded." rather than disappearing. A heading
  that vanishes when empty makes a partial note look like a complete one.
- Assessment and Plan on an unfinished visit say which stage the visit stopped at, so
  the absence reads as "the encounter is still open", not "the doctor concluded nothing".
- The assistant's differential is always under its own heading, always labelled. It is
  never printed as though a clinician wrote it.
"""

from __future__ import annotations

from typing import Union

from soap.note import (
    ASSISTANT_HEADING,
    DISCLAIMER,
    NOT_RECORDED,
    NOT_YET_RECORDED,
    NOT_ANALYSED,
    SoapNote,
)

WIDTH = 70

# A label and either one value or a list of them. An empty value still produces an
# entry — rendering decides how to say "nothing here", so both formats say it the same.
Entry = tuple[str, Union[str, list[str], None]]


def _subjective(note: SoapNote) -> list[Entry]:
    s = note.subjective
    return [
        ("Chief complaint", s.chief_complaint),
        ("Symptoms", s.symptoms),
        ("Allergies", s.allergies),
        ("Current medications", s.current_medications),
        ("Past medical history", s.past_medical_history),
        ("Social history", s.social_history),
        ("Previous diagnoses", s.previous_diagnoses),
        ("Background notes", s.background_notes),
    ]


def _objective(note: SoapNote) -> list[Entry]:
    o = note.objective
    entries: list[Entry] = [
        ("Vitals", o.vitals),
        ("Examination", o.physical_exam),
        ("Additional observations", o.observations),
    ]
    if o.reports:
        entries.append(("Reports", _report_lines(note)))
    if o.investigation_results:
        entries.append(("Investigation results", o.investigation_results))
    return entries


def _report_lines(note: SoapNote) -> list[str]:
    lines = []
    for report in note.objective.reports:
        lines.append(report.label)
        if not report.analysed:
            lines.append(f"    {NOT_ANALYSED}")
            continue
        if report.summary:
            lines.append(f"    {report.summary}")
        lines += [f"    - {value}" for value in report.abnormal]
        lines += [f"    - {pattern}" for pattern in report.patterns]
    return lines


def _assessment(note: SoapNote) -> list[Entry]:
    a = note.assessment
    diagnosis = a.working_diagnosis
    if diagnosis and a.icd10_code:
        diagnosis = f"{diagnosis} [{a.icd10_code}]"
    return [
        ("Working diagnosis", diagnosis),
        ("Reasoning", a.reasoning),
    ]


def _plan(note: SoapNote) -> list[Entry]:
    p = note.plan
    return [
        ("Investigations ordered", p.investigations),
        ("Treatment", p.medications),
        ("Notes and follow-up", p.notes),
    ]


def _is_empty(entries: list[Entry]) -> bool:
    return not any(value for _, value in entries)


def _stage_note(note: SoapNote) -> str:
    """What an empty Assessment or Plan says — naming the stage the visit stopped at."""
    return NOT_YET_RECORDED.format(status=note.visit_status)


# --------------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------------


def _text_entry(label: str, value: Union[str, list[str], None]) -> list[str]:
    if not value:
        return [f"  {label}: {NOT_RECORDED}"]
    if isinstance(value, str):
        return [f"  {label}: {value}"]
    if len(value) == 1 and not value[0].startswith(" "):
        return [f"  {label}: {value[0]}"]
    return [f"  {label}:"] + [
        # Lines already indented by their builder are nested detail, not new items.
        f"  {item}" if item.startswith(" ") else f"    - {item}"
        for item in value
    ]


def _text_section(
    letter: str, title: str, entries: list[Entry], placeholder: str = ""
) -> list[str]:
    lines = [f"{letter} — {title.upper()}", "-" * WIDTH]
    if placeholder and _is_empty(entries):
        return lines + [f"  {placeholder}", ""]
    for label, value in entries:
        lines += _text_entry(label, value)
    lines.append("")
    return lines


def to_text(note: SoapNote) -> str:
    """The note as it prints in a terminal."""
    lines = [
        "=" * WIDTH,
        f"SOAP NOTE — {note.patient_name} ({note.patient_id})",
        "=" * WIDTH,
        f"Patient:   {note.patient_summary}",
        f"Visit:     {note.visit_id} · {note.visit_date} · {note.visit_status}",
        f"Generated: {note.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
    ]
    lines += _text_section("S", "Subjective", _subjective(note))
    lines += _text_section("O", "Objective", _objective(note))
    lines += _text_section("A", "Assessment", _assessment(note), _stage_note(note))

    if note.assessment.assistant_differential:
        lines += [f"  {ASSISTANT_HEADING}:"]
        lines += [f"    {item}" for item in note.assessment.assistant_differential]
        lines.append("")

    lines += _text_section("P", "Plan", _plan(note), _stage_note(note))
    lines += ["-" * WIDTH, DISCLAIMER]
    return "\n".join(lines)


# --------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------


def _md_entry(label: str, value: Union[str, list[str], None]) -> list[str]:
    if not value:
        return [f"**{label}:** {NOT_RECORDED}", ""]
    if isinstance(value, str):
        return [f"**{label}:** {value}", ""]
    if len(value) == 1 and not value[0].startswith(" "):
        return [f"**{label}:** {value[0]}", ""]
    return [f"**{label}:**", ""] + [
        f"  {item.strip()}" if item.startswith(" ") else f"- {item}" for item in value
    ] + [""]


def _md_section(
    letter: str, title: str, entries: list[Entry], placeholder: str = ""
) -> list[str]:
    lines = [f"## {letter} — {title}", ""]
    if placeholder and _is_empty(entries):
        return lines + [placeholder, ""]
    for label, value in entries:
        lines += _md_entry(label, value)
    return lines


def to_markdown(note: SoapNote) -> str:
    """The note as Markdown, for pasting into a record system or a report."""
    lines = [
        f"# SOAP Note — {note.patient_name} ({note.patient_id})",
        "",
        f"**Patient:** {note.patient_summary}  ",
        f"**Visit:** {note.visit_id} — {note.visit_date} — {note.visit_status}  ",
        f"**Generated:** {note.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
    ]
    lines += _md_section("S", "Subjective", _subjective(note))
    lines += _md_section("O", "Objective", _objective(note))
    lines += _md_section("A", "Assessment", _assessment(note), _stage_note(note))

    if note.assessment.assistant_differential:
        lines += [f"**{ASSISTANT_HEADING}:**", ""]
        lines += [f"- {item}" for item in note.assessment.assistant_differential]
        lines.append("")

    lines += _md_section("P", "Plan", _plan(note), _stage_note(note))
    lines += ["---", "", f"*{DISCLAIMER}*"]
    return "\n".join(lines)
