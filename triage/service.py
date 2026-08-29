"""The one function a backend calls, and the pipeline it runs.

    request -> validate -> NEWS2 -> red flags -> rule floor -> AI -> decide -> result

Everything above this module is a pure function. This is the only place they are wired
together, which means the pipeline can be read in one screen and the order of the stages
is a fact about this file rather than an emergent property of who calls whom.

Integration surface for the existing backend: `triage_payload` takes a dict and returns
a dict. That is the whole API. It does not touch a database, does not import a web
framework, and raises `TriageInputError` with a readable message when the payload is
wrong - so a route handler is three lines and a try/except.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from pydantic import ValidationError

from triage import news2 as news2_engine
from triage import red_flags as red_flag_engine
from triage.decision import decide, rule_floor
from triage.interpret import interpret
from triage.rules import RuleSet, get_rules
from triage.schema import Interpretation, TriageRequest, TriageResult
from triage.validation import validate

log = logging.getLogger(__name__)


class TriageInputError(ValueError):
    """The submitted payload is not a valid triage request."""


def parse_request(payload: dict) -> TriageRequest:
    """Validate an untrusted dict into a request, with a readable error.

    Pydantic's own message is precise but long; a triage desk needs the field name and
    the problem, so the errors are flattened to one line each.
    """
    try:
        return TriageRequest.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise TriageInputError(f"Invalid triage request - {problems}") from exc


def chart_summary(profile) -> str:
    """A short plain-text projection of the record, for the interpretation prompt.

    Deliberately not `patient.history.build_clinical_context`: that renders a full
    consultation chart, which is far more than a triage decision needs and costs tokens
    on a call that has to finish in seconds. Only what changes how a presentation should
    be read at the front door goes in.

    Takes any object with the attributes rather than importing `PatientProfile`, so a
    backend can pass its own record type without adapting it first.
    """
    lines: list[str] = []
    age = getattr(profile, "age", None)
    sex = getattr(profile, "sex", "unknown")
    lines.append(f"Age {age if age is not None else 'not recorded'}, sex {sex}.")

    conditions = [c.name for c in getattr(profile, "chronic_conditions", [])]
    lines.append(
        f"Chronic conditions: {', '.join(conditions)}." if conditions
        else "Chronic conditions: none recorded."
    )

    active = getattr(profile, "active_medications", lambda: [])()
    medications = [m.name for m in active]
    lines.append(
        f"Current medications: {', '.join(medications)}." if medications
        else "Current medications: none recorded."
    )

    allergies = [a.substance for a in getattr(profile, "allergies", [])]
    lines.append(
        f"Allergies: {', '.join(allergies)}." if allergies else "Allergies: none recorded."
    )

    smoking = getattr(profile, "smoking", None)
    status = getattr(smoking, "status", "unknown")
    if status != "unknown":
        pack_years = getattr(smoking, "pack_years", None)
        detail = f" ({pack_years:g} pack-years)" if pack_years else ""
        lines.append(f"Smoking: {status}{detail}.")

    lines.append(
        "An item shown as 'none recorded' is absent from the chart, which is NOT the "
        "same as ruled out."
    )
    return "\n".join(lines)


def triage(
    request: TriageRequest,
    profile=None,
    rules: Optional[RuleSet] = None,
    llm=None,
    rules_only: Optional[bool] = None,
) -> TriageResult:
    """Triage one patient.

    `profile` is the medical record when the patient is on file, and None for a walk-in.
    `rules` and `llm` are injectable so tests and the evaluation harness can drive the
    pipeline without touching disk or the network.
    """
    started = time.monotonic()
    rules = rules or get_rules()

    if profile is not None and request.chart_summary is None:
        request = request.model_copy(update={"chart_summary": chart_summary(profile)})

    report = validate(request, rules)
    scores = news2_engine.score(
        report.normalised, rules, hypercapnic_target_range=request.hypercapnic_target_range
    )
    hits = red_flag_engine.detect(request, rules)

    # The floor is computed before the model is called, and is what the model is shown.
    # It is recomputed inside `decide` from the same inputs - deliberate duplication, so
    # that what the model was told and what the result is built from cannot drift apart.
    floor, _ = rule_floor(scores, report, hits, rules)

    interpretation: Interpretation = interpret(
        request, report, scores, hits, floor, llm=llm, rules_only=rules_only
    )

    result = decide(
        request, report, scores, hits, interpretation, rules,
        latency_ms=int((time.monotonic() - started) * 1000),
    )

    log.info(
        "triage %s: %s (floor %s, ai_escalated=%s, news2=%d, flags=%d, model=%s)",
        result.request_id, result.priority.value, result.rule_priority.value,
        result.ai_escalated, scores.aggregate, len(hits),
        "available" if interpretation.available else "unavailable",
    )
    return result


def triage_payload(
    payload: dict,
    profile=None,
    rules: Optional[RuleSet] = None,
    llm=None,
    rules_only: Optional[bool] = None,
) -> dict:
    """dict in, dict out. The integration point for the existing backend.

    Raises `TriageInputError` for a malformed payload. Every other failure - including
    the model being unreachable - produces a valid result with `interpretation.available`
    false, because a triage desk needs an answer more than it needs an exception.
    """
    request = parse_request(payload)
    result = triage(request, profile=profile, rules=rules, llm=llm, rules_only=rules_only)
    return result.model_dump(mode="json")


def retriage(
    previous: TriageResult,
    request: TriageRequest,
    profile=None,
    rules: Optional[RuleSet] = None,
    llm=None,
    rules_only: Optional[bool] = None,
) -> TriageResult:
    """Re-score a patient who is already waiting, carrying nothing over but the id.

    A new set of observations is scored on its own merits: the previous result is not
    an input, because a patient who has improved must be allowed to move down, and
    anchoring on the earlier score would prevent that. What the previous result IS used
    for is the deterioration note - a patient who has moved up while waiting is a
    different clinical situation from one who arrived at that priority, and the note
    says so where a nurse will read it.
    """
    result = triage(request, profile=profile, rules=rules, llm=llm, rules_only=rules_only)

    from triage.schema import RANK  # local import keeps the module's import list flat

    if RANK[result.priority] > RANK[previous.priority]:
        result.reasons.insert(
            0,
            f"DETERIORATED while waiting: was {previous.priority.value} at "
            f"{previous.created_at:%H:%M}, now {result.priority.value}.",
        )
        result.concerning_findings.insert(
            0, f"Priority has risen from {previous.priority.value} since arrival."
        )
    elif RANK[result.priority] < RANK[previous.priority]:
        result.reasons.insert(
            0,
            f"Improved since {previous.created_at:%H:%M} (was "
            f"{previous.priority.value}). Confirm before moving the patient down.",
        )
    return result
