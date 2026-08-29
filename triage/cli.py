"""CLI for the triage agent — the testing surface for the pipeline.

triage one patient from a JSON file:
venv\\Scripts\\python -m triage.cli assess data/triage_cases/critical_hypoxia.json

the same, without calling the model, to see what the rules alone decide:
venv\\Scripts\\python -m triage.cli assess <file> --rules-only

with the patient's record pulled from the database:
venv\\Scripts\\python -m triage.cli assess <file> --patient P-001

the full result as JSON, which is what a backend receives:
venv\\Scripts\\python -m triage.cli assess <file> --json

sort a whole waiting room, one JSON file per patient or one file holding a list:
venv\\Scripts\\python -m triage.cli room data/triage_cases/*.json

check the rule set loads and is internally consistent:
venv\\Scripts\\python -m triage.cli rules

Nothing here writes to the database unless --save is given. Running an assessment and
walking away leaves no record behind, matching how chatbot.cli treats a consultation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from triage.queue import WaitingRoom
from triage.rules import RuleSetError, get_rules
from triage.schema import Priority, TriageResult
from triage.service import TriageInputError, parse_request, triage

# Plain ASCII markers. The Windows console this project is developed on renders box
# drawing and emoji unreliably, and a triage priority is the last thing that should
# arrive as a row of question marks.
_MARKERS = {
    Priority.CRITICAL: "[!!!]",
    Priority.URGENT: "[!! ]",
    Priority.STANDARD: "[ . ]",
    Priority.LOW: "[   ]",
}


def _load_payloads(paths: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            # The evaluation cases carry an `expected` label and underscore-prefixed
            # notes alongside the request. Stripped here so one set of files drives both
            # the CLI and the evaluation harness.
            payloads.append(
                {k: v for k, v in item.items() if k != "expected" and not k.startswith("_")}
            )
    return payloads


def _profile_for(patient_id: Optional[str]):
    if not patient_id:
        return None
    from storage import db
    from storage.patient_repository import SqlitePatientRepository

    conn = db.connect()
    try:
        profile = SqlitePatientRepository(conn).get(patient_id)
    finally:
        conn.close()
    if profile is None:
        print(f"No patient with id {patient_id}; continuing without a record.", file=sys.stderr)
    return profile


def _print_result(result: TriageResult) -> None:
    marker = _MARKERS[result.priority]
    print()
    print(f"{marker} {result.priority.value}")
    print(f"      {result.recommended_action}")
    if result.status != "OK":
        print(f"      STATUS: {result.status}")
    print()

    if result.news2:
        news2 = result.news2
        print(
            f"NEWS2 {news2.aggregate} "
            f"({news2.scored_count}/{news2.expected_count} parameters, Scale {news2.scale})"
        )
        for parameter in news2.parameters:
            mark = "  " if parameter.scored and (parameter.score or 0) == 0 else "->"
            print(f"  {mark} {parameter.describe()}")
        print()

    def section(title: str, lines: list[str]) -> None:
        if not lines:
            return
        print(f"{title}:")
        for line in lines:
            print(f"  - {line}")
        print()

    section("Why", result.reasons)
    section("Concerning findings", result.concerning_findings)
    section("Missing information", result.missing_information)
    section("Data quality", result.data_quality_issues)
    section("Context from the record", result.context_factors)

    interpretation = result.interpretation
    if interpretation.available:
        if interpretation.summary:
            print(f"AI summary: {interpretation.summary}")
        section("AI risk signals", interpretation.risk_signals)
        if result.ai_escalated:
            print(f"AI ESCALATED one level: {interpretation.escalation_reason}")
            print()
    else:
        print(f"AI interpretation unavailable ({interpretation.failure}).")
        print("This result is rules-only and complete without it.")
        print()

    print(
        f"Human review required. Rules floor was {result.rule_priority.value}; "
        f"ruleset {result.ruleset_version}; engine {result.engine_version}."
    )


def _cmd_assess(args) -> int:
    payloads = _load_payloads([args.file])
    if len(payloads) != 1:
        print(f"{args.file} holds {len(payloads)} cases; use `room` for more than one.")
        return 2

    payload = payloads[0]
    if args.patient:
        payload["patient_id"] = args.patient

    request = parse_request(payload)
    result = triage(request, profile=_profile_for(request.patient_id), rules_only=args.rules_only)

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    else:
        _print_result(result)

    if args.save:
        _save(result)
    return 0


def _save(result: TriageResult) -> None:
    from storage import db
    from storage.triage_repository import SqliteTriageRepository

    conn = db.connect()
    try:
        SqliteTriageRepository(conn).save_result(result)
    finally:
        conn.close()
    print(f"Recorded as {result.request_id}.")


def _cmd_room(args) -> int:
    rules = get_rules()
    room = WaitingRoom()
    for payload in _load_payloads(args.files):
        request = parse_request(payload)
        result = triage(request, rules=rules, rules_only=args.rules_only)
        room.admit(result, display_name=request.display_name)

    print()
    print("WAITING ROOM")
    print("=" * 68)
    for position, entry in enumerate(room.ordered(rules), 1):
        name = entry.display_name or entry.patient_id or entry.result.request_id
        result = entry.result
        marker = _MARKERS[entry.priority]
        flags = f"  flags: {len(result.red_flags)}" if result.red_flags else ""
        news2 = f"NEWS2 {result.news2.aggregate}" if result.news2 else "no score"
        overridden = " (clinician override)" if result.override else ""
        print(f"{position:>2}. {marker} {entry.priority.value:<9} {name:<28} {news2}{flags}{overridden}")
        if result.reasons:
            print(f"       {result.reasons[0]}")
    print("=" * 68)

    counts = room.counts()
    print(
        "  ".join(f"{priority.value}: {counts[priority]}" for priority in Priority)
        + f"    total waiting: {len(room)}"
    )
    return 0


def _cmd_rules(args) -> int:
    try:
        rules = get_rules(refresh=True)
    except RuleSetError as exc:
        print(f"RULE SET WILL NOT LOAD\n\n{exc}", file=sys.stderr)
        return 1

    print(f"Rule set {rules.ruleset_version} loaded and internally consistent.")
    print(f"  Source:     {rules.news2.source.citation()}")
    print(f"  Population: adults {rules.population.min_age_years}+", end="")
    print(", pregnancy excluded" if rules.population.excludes_pregnancy else "")
    print(f"  Red flags:  {len(rules.red_flags.flags)}")
    print(f"  Tie-break:  {rules.waiting_room.tie_break}")
    print()
    if rules.is_verified():
        print(f"Verification: {rules.verification_status}")
    else:
        print("!! THRESHOLDS NOT YET VERIFIED AGAINST THE SOURCE DOCUMENT")
        print(f"   {rules.verification_status}")

    if args.flags:
        print()
        for flag in rules.red_flags.flags:
            print(f"  {flag.floor.value:<9} {flag.id:<24} {flag.label}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="triage", description="Patient triage agent (decision support, not a decision)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    assess = subparsers.add_parser("assess", help="triage one patient from a JSON file")
    assess.add_argument("file")
    assess.add_argument("--patient", help="pull this patient's record into the assessment")
    assess.add_argument("--rules-only", action="store_true", help="skip the model")
    assess.add_argument("--json", action="store_true", help="print the full result as JSON")
    assess.add_argument("--save", action="store_true", help="record the result in the database")
    assess.set_defaults(func=_cmd_assess)

    room = subparsers.add_parser("room", help="triage several patients and order them")
    room.add_argument("files", nargs="+")
    room.add_argument("--rules-only", action="store_true", help="skip the model")
    room.set_defaults(func=_cmd_room)

    rules = subparsers.add_parser("rules", help="check the rule set loads and is consistent")
    rules.add_argument("--flags", action="store_true", help="list every red flag")
    rules.set_defaults(func=_cmd_rules)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TriageInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuleSetError as exc:
        print(f"RULE SET ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
