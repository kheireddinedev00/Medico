"""Checking our transcription of the NEWS2 tables against an independent scoring.

    venv\\Scripts\\python -m evaluation.verify_news2
    venv\\Scripts\\python -m evaluation.verify_news2 --show 20   (disagreements in full)

This is NOT an evaluation of the triage agent, and must not be reported as one.

`data/triage_rules.json` carries the seven NEWS2 parameter tables, typed out by hand from
the published scale, under a `verification_status` field that says so and asks for them
to be checked before the capstone defence. Reading them cell by cell catches most errors
and is exactly the kind of check that goes wrong the same way twice.

So instead: somebody else scored a thousand sets of observations against the same
published scale, and their answers are on Kaggle. Where we agree, two independent
transcriptions agree. Where we disagree, one of us is wrong about a specific cell in a
specific table, and the row says which - which is far more useful than a second read.

What this deliberately does NOT do
----------------------------------
It does not call `triage()`. It runs `news2.score` plus the two escalation rules that
vitals alone can trigger - the aggregate band and the single-parameter red score - because
the tables are the thing under test. Red flags, the age gate and the model need a
narrative and an age, which this dataset does not have; they are covered by
`evaluation/run_eval.py` against `data/triage_cases/`.

It also does not treat `Risk_Level` as a reference standard. That column is not a
clinician's judgement: it is the NEWS2 risk category recomputed from the same seven
inputs. Scoring the agent against it would be scoring NEWS2 against NEWS2 and would pass
by construction. The reasoning is in `data/verification/sources.json`.

Not a single clinical number appears below. Bands, the red-score trigger and the
priorities all come from the rule set - the same object the engine loads - so this file
cannot drift away from what ships.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from config import DATA_DIR
from triage import news2 as news2_engine

from triage.rules import RuleSet, get_rules
from triage.schema import News2Result, NormalisedVitals, Priority, highest

DEFAULT_CSV = DATA_DIR / "verification" / "Health_Risk_Dataset.csv"

# The dataset's ACVPU letters in our vocabulary. Renaming, not interpretation.
ACVPU = {"A": "alert", "V": "voice", "C": "confusion", "P": "pain", "U": "unresponsive"}

# Their four risk classes ascending, against our four priorities ascending. The only
# assumption in the file; it is an ordering rather than a threshold, and were it wrong
# the agreement rate would collapse to noise rather than sitting near 100%.
RISK_TO_PRIORITY = {
    "Normal": Priority.LOW,
    "Low": Priority.STANDARD,
    "Medium": Priority.URGENT,
    "High": Priority.CRITICAL,
}

# A disagreement we have already adjudicated, so a re-run does not re-litigate it and a
# NEW disagreement is not lost in the noise of an old one. See the printout for the
# argument; in short, the published Scale 2 table scores an air-breathing patient and an
# oxygenated one differently above the target range, and their scoring does not.
ADJUDICATED = "scale 2, breathing air, saturation at or above the target range"


def our_priority(result: News2Result, rules: RuleSet) -> Priority:
    """What our engine ranks these observations, using only what vitals can decide."""
    candidates = []
    band = rules.escalation.band_for(result.aggregate)
    if band is not None:
        candidates.append(band.priority)
    if result.single_parameter_red:
        candidates.append(rules.escalation.single_parameter_floor.priority)
    return highest(*candidates)


def vitals_from(row: dict) -> NormalisedVitals:
    """One CSV row as normalised vitals.

    The dataset has no diastolic, weight or height, and NEWS2 scores none of them, so
    they stay None rather than being invented.
    """
    return NormalisedVitals(
        respiratory_rate=int(row["Respiratory_Rate"]),
        spo2=float(row["Oxygen_Saturation"]),
        systolic_bp=int(row["Systolic_BP"]),
        heart_rate=int(row["Heart_Rate"]),
        temperature_c=float(row["Temperature"]),
        consciousness=ACVPU[row["Consciousness"].strip().upper()],
        on_oxygen=row["On_Oxygen"].strip() == "1",
    )


def classify(result: News2Result, rules: RuleSet) -> str:
    """Which known argument, if any, accounts for a disagreement on this row."""
    on_air = not any(
        p.parameter == "air_or_oxygen" and p.value == "oxygen" for p in result.parameters
    )
    at_target = any(
        p.parameter == "spo2" and p.scored and p.score == 0 and p.value not in (None, "")
        for p in result.parameters
    )
    if result.scale == 2 and on_air and at_target:
        return ADJUDICATED
    return "unexplained"


def render(row: dict, result: News2Result) -> str:
    scored = "  ".join(
        f"{p.parameter}={p.value or '-'}:{p.score if p.scored else 'x'}"
        for p in result.parameters
    )
    return f"    scale {result.scale}  {scored}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--show", type=int, default=5, help="disagreeing rows to print")
    args = parser.parse_args()

    if not args.csv.is_file():
        print(f"Dataset not found: {args.csv}")
        print("Gitignored on purpose - data/verification/sources.json says where to get it.")
        return 2

    rules = get_rules()
    rows = list(csv.DictReader(args.csv.open(encoding="utf-8-sig")))

    agreed = Counter()
    seen = Counter()
    disagreements: list[tuple[dict, News2Result, Priority, Priority, str]] = []

    for row in rows:
        # Scale 2 is stated per row by the dataset. That is the only way it may ever be
        # set - inferring it from a diagnosis is the one inference this project refuses.
        scale2 = row["O2_Scale"].strip() == "2"
        result = news2_engine.score(vitals_from(row), rules, hypercapnic_target_range=scale2)

        theirs = RISK_TO_PRIORITY[row["Risk_Level"].strip()]
        ours = our_priority(result, rules)
        key = f"scale {result.scale}"
        seen[key] += 1

        if ours == theirs:
            agreed[key] += 1
        else:
            disagreements.append((row, result, theirs, ours, classify(result, rules)))

    total = len(rows)
    matched = sum(agreed.values())
    unexplained = [d for d in disagreements if d[4] == "unexplained"]

    print(f"NEWS2 transcription check - {args.csv.name}")
    print(f"ruleset {rules.ruleset_version}\n")
    print(f"  rows scored     {total}")
    print(f"  agreement       {matched}/{total}  ({matched / total:.1%})")
    for key in sorted(seen):
        print(f"    {key:<9}     {agreed[key]}/{seen[key]}  ({agreed[key] / seen[key]:.1%})")

    buckets = Counter(d[4] for d in disagreements)
    if disagreements:
        print(f"\n  {len(disagreements)} disagreement(s):")
        for reason, count in buckets.most_common():
            print(f"    {count:>4}  {reason}")

    for reason, _ in buckets.most_common():
        examples = [d for d in disagreements if d[4] == reason][: args.show]
        if not examples:
            continue
        print(f"\n  --- {reason} ---")
        for row, result, theirs, ours, _ in examples:
            print(
                f"\n  {row['Patient_ID']}  theirs={theirs.value}  ours={ours.value}"
                f"  aggregate={result.aggregate}"
            )
            print(render(row, result))

    if buckets.get(ADJUDICATED):
        print(
            f"\n  On the {buckets[ADJUDICATED]} Scale 2 rows: our tables are the published"
        )
        print("  ones and theirs are not. NEWS2 Scale 2 splits saturation into an ON AIR")
        print("  column and an ON OXYGEN column, and the two differ above the 88-92% target:")
        print("  on oxygen, 93% and up scores 1 to 3, because over-oxygenating a patient in")
        print("  hypercapnic respiratory failure is itself the danger; on air there is no")
        print("  oxygen to withdraw and the same saturation scores 0. Every row above is a")
        print("  patient breathing air, scored as though they were on oxygen. Their labels")
        print("  collapse the two columns into one. Ours do not.")

    if unexplained:
        print(f"\n  {len(unexplained)} disagreement(s) NOT accounted for by a known argument.")
        print("  Each is a cell in a parameter table to re-read against the source.")
    elif disagreements:
        print("\n  No unexplained disagreements: every difference is the one argued above.")
    else:
        print("\n  No disagreements at all.")

    print("\n  Scope: the seven parameter tables and the two vitals-only escalation rules.")
    print("  Not an evaluation of the triage agent - that is evaluation/run_eval.py.")

    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
