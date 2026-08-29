"""The one model call, and the guarantee that it cannot break triage.

Everything in this module is written around a single operational fact: the model is a
free-tier endpoint that will be slow, rate-limited or unreachable a meaningful fraction
of the time. A waiting room that stops sorting patients when OpenRouter is busy is not a
triage system, so the model is treated as an enrichment that may simply not arrive.

Every failure path - no key, timeout, 429, malformed JSON, schema violation - returns an
`Interpretation` with `available=False` and a human-readable `failure`. None of them
raise. `decision.py` produces a complete, defensible result from either kind.

The other half of the guarantee is in the schema: `RawInterpretation` has no priority
field. The model is not trusted to be bounded by the prompt; it is structurally unable
to return a priority, and the only lever it has - `escalate` - can only move a patient
up. See `decision.py` for where that is enforced.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from clinical.json_reply import ReplyError
from config import TRIAGE_LLM_RETRIES, TRIAGE_LLM_TIMEOUT, TRIAGE_MODEL, TRIAGE_RULES_ONLY
from triage.news2 import describe as describe_news2
from triage.prompt import SYSTEM_PROMPT, render_request
from triage.schema import (
    Interpretation,
    News2Result,
    NormalisedVitals,
    Priority,
    RawInterpretation,
    RedFlagHit,
    TriageRequest,
    ValidationReport,
)

log = logging.getLogger(__name__)

# Cap on what the model may add. A triage card a nurse reads in three seconds cannot
# carry fifteen bullet points, and a model that returns fifteen is padding.
MAX_ITEMS = 6
MAX_ITEM_CHARS = 240


class InterpretationError(ReplyError):
    """The model's reply could not be parsed or failed validation."""


def format_presentation(request: TriageRequest) -> str:
    lines = [f"Chief complaint: {request.chief_complaint or 'not recorded'}"]
    if request.symptoms:
        lines.append(f"Symptoms: {'; '.join(request.symptoms)}")
    if request.notes:
        lines.append(f"Notes: {request.notes}")
    age = f"{request.age_years}" if request.age_years is not None else "not recorded"
    lines.append(f"Age: {age}    Sex: {request.sex}")
    return "\n".join(lines)


def format_vitals(vitals: NormalisedVitals, report: ValidationReport) -> str:
    """Observations as recorded, with absences stated as absences.

    Every missing value is printed as "not recorded" rather than omitted. A model shown
    a short list of normal observations reads a reassuring patient; a model shown that
    four of seven were never taken reads an unassessed one, which is the truth.
    """
    rows = [
        ("Respiratory rate", vitals.respiratory_rate, "/min"),
        ("Oxygen saturation", vitals.spo2, "%"),
        ("Systolic BP", vitals.systolic_bp, " mmHg"),
        ("Diastolic BP", vitals.diastolic_bp, " mmHg"),
        ("Heart rate", vitals.heart_rate, " bpm"),
        ("Temperature", vitals.temperature_c, " C"),
    ]
    lines = [
        f"{label}: {value:g}{unit}" if value is not None else f"{label}: not recorded"
        for label, value, unit in rows
    ]
    if vitals.on_oxygen is None:
        lines.append("Supplemental oxygen: not recorded")
    else:
        lines.append(f"Supplemental oxygen: {'yes' if vitals.on_oxygen else 'no (on air)'}")
    lines.append(f"Level of consciousness: {vitals.consciousness}")

    problems = [i.describe() for i in report.issues if i.issue != "missing"]
    if problems:
        lines.append("")
        lines.append("Data quality problems with these observations:")
        lines += [f"  - {p}" for p in problems]
    return "\n".join(lines)


def format_rules_outcome(
    news2: News2Result, hits: list[RedFlagHit], floor: Priority
) -> str:
    """What the deterministic layer already decided, stated as settled."""
    lines = [
        f"NEWS2 aggregate: {news2.aggregate} "
        f"(from {news2.scored_count} of {news2.expected_count} parameters, Scale {news2.scale})"
    ]
    abnormal = describe_news2(news2)
    if abnormal:
        lines += [f"  - {line}" for line in abnormal]
    else:
        lines.append("  - No abnormal parameter among those recorded.")

    if hits:
        lines.append("Red flags already detected by the rules:")
        lines += [f"  - {hit.label} (matched: {hit.matched})" for hit in hits]
    else:
        lines.append("Red flags already detected by the rules: none.")

    lines.append(f"PRIORITY FLOOR ALREADY SET: {floor.value}. You cannot go below this.")
    return "\n".join(lines)


def _clean(items: list[str]) -> list[str]:
    """Trim, drop empties and duplicates, and cap the length. Order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = " ".join(str(item).split())[:MAX_ITEM_CHARS]
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= MAX_ITEMS:
            break
    return out


def bound(raw: RawInterpretation, model: Optional[str], latency_ms: int) -> Interpretation:
    """Everything the model returned, trimmed and made safe to display.

    An escalation with no stated reason is refused. A model that wants a patient moved
    up the queue has to say why, because the nurse acting on it needs something to read
    - and an unexplained escalation is indistinguishable from a parsing accident.
    """
    escalate = bool(raw.escalate)
    reason = " ".join((raw.escalation_reason or "").split())[:MAX_ITEM_CHARS] or None
    if escalate and not reason:
        escalate = False
        log.warning("Triage model set escalate=true with no reason; escalation dropped")

    return Interpretation(
        available=True,
        risk_signals=_clean(raw.risk_signals),
        escalate=escalate,
        escalation_reason=reason if escalate else None,
        concerning_findings=_clean(raw.concerning_findings),
        missing_information=_clean(raw.missing_information),
        context_factors=_clean(raw.context_factors),
        summary=(" ".join((raw.summary or "").split())[:MAX_ITEM_CHARS] or None),
        model=model,
        latency_ms=latency_ms,
    )


def interpret(
    request: TriageRequest,
    report: ValidationReport,
    news2: News2Result,
    hits: list[RedFlagHit],
    floor: Priority,
    llm=None,
    rules_only: Optional[bool] = None,
) -> Interpretation:
    """Ask the model to read the presentation. Never raises.

    `llm` is injectable so the whole pipeline can be tested without a network call, and
    so the evaluation harness can drive a deliberately hostile model.
    """
    if rules_only is None:
        rules_only = TRIAGE_RULES_ONLY
    if rules_only:
        return Interpretation(available=False, failure="rules-only mode: model not called")

    message = render_request(
        presentation=format_presentation(request),
        vitals_block=format_vitals(report.normalised, report),
        rules_block=format_rules_outcome(news2, hits, floor),
        chart_block=request.chart_summary,
    )

    started = time.monotonic()
    try:
        # Imported here rather than at module scope so that importing the triage package
        # does not require langchain to be installed or a key to be configured. The
        # rules-only path stays usable in an environment with neither.
        from chatbot.llm import get_llm, run_structured

        client = llm if llm is not None else get_llm(
            temperature=0.1,
            max_retries=TRIAGE_LLM_RETRIES,
            model=TRIAGE_MODEL,
            timeout=TRIAGE_LLM_TIMEOUT,
        )
        raw = run_structured(
            SYSTEM_PROMPT, [message], RawInterpretation, llm=client,
            error_cls=InterpretationError,
        )
    except Exception as exc:  # noqa: BLE001 - every failure is a degraded result, not a crash
        elapsed = int((time.monotonic() - started) * 1000)
        log.warning("Triage interpretation unavailable after %dms: %s", elapsed, exc)
        return Interpretation(
            available=False,
            failure=f"{type(exc).__name__}: {exc}"[:MAX_ITEM_CHARS],
            model=TRIAGE_MODEL,
            latency_ms=elapsed,
        )

    return bound(raw, TRIAGE_MODEL, int((time.monotonic() - started) * 1000))
