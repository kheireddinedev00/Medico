"""Measuring the triage agent against labelled synthetic cases.

    venv\\Scripts\\python -m evaluation.run_eval --rules-only
    venv\\Scripts\\python -m evaluation.run_eval                    (with the model)
    venv\\Scripts\\python -m evaluation.run_eval --repeat 3         (consistency)

The metric that matters is not accuracy. In triage the two kinds of error are not
comparable: sending a well patient to the front of the queue wastes a clinician's time,
and leaving a critically ill patient in the waiting room does not. So the headline
number here is **critical sensitivity** - of the patients whose reference label is
CRITICAL, how many did the agent rank CRITICAL - and its complement, the critical
false-negative count, which is the number this project should be judged on.

Under- and over-triage are reported separately for the same reason. A single accuracy
figure averages them together and hides exactly the asymmetry that makes triage hard.

Everything here runs against `data/triage_cases/`, which is synthetic. No real patient
data is used, and the reference labels were fixed when the cases were written.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from config import TRIAGE_CASES_DIR
from triage.rules import get_rules
from triage.schema import RANK, Priority, TriageResult
from triage.service import parse_request, triage

ORDER = [Priority.CRITICAL, Priority.URGENT, Priority.STANDARD, Priority.LOW]


class Case:
    def __init__(self, payload: dict):
        self.expected = Priority(payload["expected"])
        self.why: Optional[str] = payload.get("_why")
        self.payload = {
            k: v for k, v in payload.items() if k != "expected" and not k.startswith("_")
        }
        self.id = self.payload.get("id", "unnamed")

    def request(self):
        return parse_request(self.payload)


def load_cases(path: Optional[Path] = None) -> list[Case]:
    directory = Path(path or TRIAGE_CASES_DIR)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else [directory]
    cases: list[Case] = []
    for file in files:
        raw = json.loads(file.read_text(encoding="utf-8"))
        for item in raw if isinstance(raw, list) else [raw]:
            if "expected" in item:
                cases.append(Case(item))
    return cases


def _bar(count: int, total: int, width: int = 24) -> str:
    filled = 0 if total == 0 else round(width * count / total)
    return "#" * filled + "." * (width - filled)


def evaluate(cases: list[Case], rules_only: bool, repeat: int) -> dict:
    rules = get_rules()
    rows: list[dict] = []
    per_case_labels: dict[str, set[str]] = {}

    for case in cases:
        latencies: list[float] = []
        labels: set[str] = set()
        result: Optional[TriageResult] = None
        for _ in range(repeat):
            started = time.monotonic()
            result = triage(case.request(), rules=rules, rules_only=rules_only)
            latencies.append((time.monotonic() - started) * 1000)
            labels.add(result.priority.value)
        assert result is not None
        per_case_labels[case.id] = labels
        rows.append(
            {
                "case": case.id,
                "expected": case.expected,
                "actual": result.priority,
                "rule_floor": result.rule_priority,
                "ai_escalated": result.ai_escalated,
                "ai_available": result.interpretation.available,
                "status": result.status,
                "news2": result.news2.aggregate if result.news2 else None,
                "flags": len(result.red_flags),
                "latency_ms": statistics.median(latencies),
                "why": case.why,
            }
        )

    total = len(rows)
    exact = sum(1 for r in rows if r["actual"] == r["expected"])
    under = [r for r in rows if RANK[r["actual"]] < RANK[r["expected"]]]
    over = [r for r in rows if RANK[r["actual"]] > RANK[r["expected"]]]

    critical = [r for r in rows if r["expected"] == Priority.CRITICAL]
    critical_caught = [r for r in critical if r["actual"] == Priority.CRITICAL]
    critical_missed = [r for r in critical if r["actual"] != Priority.CRITICAL]

    # A patient the agent could not fully assess must never be ranked LOW.
    incomplete = [r for r in rows if r["status"] == "INSUFFICIENT_DATA"]
    incomplete_ranked_low = [r for r in incomplete if r["actual"] == Priority.LOW]

    inconsistent = {c: sorted(l) for c, l in per_case_labels.items() if len(l) > 1}

    return {
        "rows": rows,
        "total": total,
        "exact": exact,
        "under": under,
        "over": over,
        "critical": critical,
        "critical_caught": critical_caught,
        "critical_missed": critical_missed,
        "incomplete": incomplete,
        "incomplete_ranked_low": incomplete_ranked_low,
        "inconsistent": inconsistent,
        "latencies": [r["latency_ms"] for r in rows],
        "ai_used": sum(1 for r in rows if r["ai_available"]),
        "ai_escalations": [r for r in rows if r["ai_escalated"]],
        "repeat": repeat,
        "rules_only": rules_only,
    }


def report(summary: dict) -> None:
    total = summary["total"]
    rows = summary["rows"]

    print()
    print("=" * 74)
    print("TRIAGE AGENT EVALUATION")
    mode = "rules only (model not called)" if summary["rules_only"] else "rules + AI interpretation"
    print(f"  mode: {mode}    cases: {total}    runs per case: {summary['repeat']}")
    print("=" * 74)

    print()
    print("SAFETY - the number this system is judged on")
    critical_total = len(summary["critical"])
    caught = len(summary["critical_caught"])
    missed = summary["critical_missed"]
    sensitivity = 0.0 if critical_total == 0 else caught / critical_total
    print(f"  Critical sensitivity      {caught}/{critical_total}  ({sensitivity:.1%})")
    print(f"  Critical patients missed  {len(missed)}")
    for row in missed:
        print(f"    !! {row['case']}: expected CRITICAL, got {row['actual'].value}")
    if not missed:
        print("    No critically ill patient was ranked below CRITICAL.")

    print()
    print("AGREEMENT WITH THE REFERENCE LABELS")
    print(f"  Exact agreement           {summary['exact']}/{total}  ({summary['exact']/total:.1%})")
    print(f"  Under-triaged (too low)   {len(summary['under'])}")
    for row in summary["under"]:
        print(f"    - {row['case']}: {row['expected'].value} -> {row['actual'].value}")
    print(f"  Over-triaged (too high)   {len(summary['over'])}")
    for row in summary["over"]:
        print(f"    - {row['case']}: {row['expected'].value} -> {row['actual'].value}")

    print()
    print("CONFUSION MATRIX  (rows = expected, columns = assigned)")
    matrix = Counter((r["expected"], r["actual"]) for r in rows)
    header = "".join(f"{p.value[:4]:>8}" for p in ORDER)
    print(f"  {'':<10}{header}")
    for expected in ORDER:
        cells = "".join(f"{matrix[(expected, actual)]:>8}" for actual in ORDER)
        print(f"  {expected.value:<10}{cells}")

    print()
    print("DISTRIBUTION")
    assigned = Counter(r["actual"] for r in rows)
    for priority in ORDER:
        count = assigned[priority]
        print(f"  {priority.value:<10} {count:>3}  {_bar(count, total)}")

    print()
    print("MISSING-DATA HANDLING")
    print(f"  Cases with incomplete observations   {len(summary['incomplete'])}")
    print(f"  Of those, ranked LOW                 {len(summary['incomplete_ranked_low'])}", end="")
    print("   <- must be 0" if summary["incomplete_ranked_low"] else "   (correct)")
    for row in summary["incomplete_ranked_low"]:
        print(f"    !! {row['case']} was ranked LOW despite incomplete observations")

    print()
    print("CONSISTENCY")
    if summary["repeat"] < 2:
        print("  Not measured. Re-run with --repeat 3 to check the same input scores the same.")
    elif summary["inconsistent"]:
        print(f"  {len(summary['inconsistent'])} case(s) returned different priorities across runs:")
        for case, labels in summary["inconsistent"].items():
            print(f"    !! {case}: {' / '.join(labels)}")
    else:
        print(f"  All {total} cases returned the same priority on every run.")

    print()
    print("AI CONTRIBUTION")
    if summary["rules_only"]:
        print("  Model not called. Every result above came from the deterministic layers,")
        print("  which is the evidence that the agent works when the model is unavailable.")
    else:
        print(f"  Interpretation available for {summary['ai_used']}/{total} cases")
        print(f"  Escalations applied          {len(summary['ai_escalations'])}")
        for row in summary["ai_escalations"]:
            print(f"    - {row['case']}: {row['rule_floor'].value} -> {row['actual'].value}")

    print()
    print("LATENCY (per case, median of runs)")
    latencies = sorted(summary["latencies"])
    if latencies:
        print(f"  median {statistics.median(latencies):>8.1f} ms")
        print(f"  p95    {latencies[max(0, int(len(latencies) * 0.95) - 1)]:>8.1f} ms")
        print(f"  max    {max(latencies):>8.1f} ms")

    print()
    print("-" * 74)
    print("WHAT THIS DOES AND DOES NOT SHOW")
    print("  The cases and the rules were written by the same authors, so agreement here")
    print("  measures internal consistency, not clinical validity. It demonstrates that")
    print("  the rules behave as specified and that the safety properties hold - nothing")
    print("  more. A real accuracy figure needs cases labelled independently, ideally by")
    print("  a triage nurse who has not seen the rule set.")
    print("-" * 74)
    print()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_eval", description="Evaluate the triage agent against synthetic cases."
    )
    parser.add_argument("--cases", help="case file or directory", default=None)
    parser.add_argument("--rules-only", action="store_true", help="do not call the model")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case, for consistency")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases) if args.cases else None)
    if not cases:
        print("No labelled cases found.", file=sys.stderr)
        return 1

    summary = evaluate(cases, rules_only=args.rules_only, repeat=max(1, args.repeat))

    if args.json:
        print(json.dumps(
            {
                "total": summary["total"],
                "exact_agreement": summary["exact"] / summary["total"],
                "critical_sensitivity": (
                    len(summary["critical_caught"]) / len(summary["critical"])
                    if summary["critical"] else None
                ),
                "critical_missed": [r["case"] for r in summary["critical_missed"]],
                "under_triaged": [r["case"] for r in summary["under"]],
                "over_triaged": [r["case"] for r in summary["over"]],
                "median_latency_ms": statistics.median(summary["latencies"]),
            },
            indent=2,
        ))
    else:
        report(summary)

    # Non-zero exit when a critical patient was missed, so this can gate a commit.
    return 1 if summary["critical_missed"] or summary["incomplete_ranked_low"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
