"""Where the priority is decided. The safety boundary of the whole agent.

Everything upstream produces evidence; this module is the only place that turns evidence
into a priority, and it is small on purpose - a safety property you cannot read in one
sitting is not a safety property.

The rule, stated once:

    FINAL = max(rule_floor, ai_adjustment)

`rule_floor` is the most urgent of four deterministic outcomes: the NEWS2 band, the
single-parameter red score, any red flag that fired, and the floor for incomplete or
out-of-scope data. `ai_adjustment` is at most one level above the floor and is only
present when the model asked for it with a reason.

Which gives the three properties the design was built for:

1. **The model cannot de-escalate.** Not by policy, but arithmetically: `highest()` only
   ever returns the more urgent of its arguments, and the model has no field in which to
   express a lower priority even if it wanted to.
2. **The model cannot run away with it.** Escalation raises a patient to URGENT and
   stops there, whatever the model says. It can always get someone looked at promptly
   when it spots what the phrase list missed; it can never, on its own, declare a
   CRITICAL. That claim belongs to the deterministic rules and the clinician.
3. **The agent works without the model.** Take the interpretation away and the floor is
   unchanged, which is why an unreachable OpenRouter degrades this system rather than
   stopping it.
"""

from __future__ import annotations

from typing import Optional

from triage.red_flags import floor_from
from triage.rules import ENGINE_VERSION, RuleSet
from triage.schema import (
    Interpretation,
    News2Result,
    Priority,
    RANK,
    RedFlagHit,
    TriageRequest,
    TriageResult,
    ValidationReport,
    highest,
)
from triage.validation import label_for

def ai_ceiling(rules: RuleSet) -> Priority:
    """The highest priority the model's escalation can reach on its own."""
    return rules.escalation.ai_escalation.to_priority


def rule_floor(
    news2: News2Result,
    report: ValidationReport,
    hits: list[RedFlagHit],
    rules: RuleSet,
) -> tuple[Priority, list[str]]:
    """The deterministic priority, and the reasons for it in plain sentences.

    Reasons are collected for every rule that contributed, not only the winning one. A
    nurse looking at a CRITICAL wants to know it was the saturation AND the complaint,
    because that is what tells them how sick the patient is.
    """
    escalation = rules.escalation
    reasons: list[str] = []
    candidates: list[Priority] = []

    if report.out_of_scope:
        floor = escalation.out_of_scope_floor.priority
        candidates.append(floor)
        reasons.append(
            report.out_of_scope_reason
            or "Patient falls outside the validated population for this rule set."
        )
        # Red flags still apply - they are not physiology and do not depend on the scale
        # being validated for this patient.
        flag_floor = floor_from(hits)
        if flag_floor is not None:
            candidates.append(flag_floor)
            reasons += [f"{hit.label}: {hit.reason}" for hit in hits]
        return highest(*candidates), reasons

    band = escalation.band_for(news2.aggregate)
    if band is not None:
        candidates.append(band.priority)
        reasons.append(
            f"NEWS2 aggregate {news2.aggregate} "
            f"({news2.scored_count} of {news2.expected_count} parameters recorded)"
        )

    if news2.single_parameter_red:
        candidates.append(escalation.single_parameter_floor.priority)
        parameters = ", ".join(news2.red_parameters)
        reasons.append(f"{escalation.single_parameter_floor.reason} ({parameters})")

    flag_floor = floor_from(hits)
    if flag_floor is not None:
        candidates.append(flag_floor)
        reasons += [f"{hit.label}: {hit.reason}" for hit in hits]

    if report.missing_required:
        candidates.append(escalation.insufficient_data_floor.priority)
        missing = ", ".join(label_for(field) for field in report.missing_required)
        reasons.append(
            f"Observations incomplete ({missing}) - cannot be shown to be low risk"
        )

    return highest(*candidates), reasons


def decide(
    request: TriageRequest,
    report: ValidationReport,
    news2: News2Result,
    hits: list[RedFlagHit],
    interpretation: Interpretation,
    rules: RuleSet,
    latency_ms: Optional[int] = None,
) -> TriageResult:
    """Assemble the final result. Pure: no I/O, no clock beyond the timestamp."""
    floor, reasons = rule_floor(news2, report, hits, rules)

    ai_priority: Optional[Priority] = None
    if interpretation.available and interpretation.escalate:
        ai_priority = ai_ceiling(rules)

    final = highest(floor, ai_priority)
    escalated = ai_priority is not None and RANK[final] > RANK[floor]

    if escalated and interpretation.escalation_reason:
        reasons.append(
            f"AI interpretation escalated to {final.value}: {interpretation.escalation_reason}"
        )

    status = "OK"
    if report.out_of_scope:
        status = "OUT_OF_SCOPE"
    elif report.missing_required:
        status = "INSUFFICIENT_DATA"

    concerning = [p.describe() for p in news2.abnormal()]
    concerning += [f"{hit.label} (from: {hit.matched})" for hit in hits]
    concerning += interpretation.concerning_findings

    missing = [
        f"{label_for(field)} not recorded" for field in report.missing_required
    ] + interpretation.missing_information

    return TriageResult(
        request_id=request.id,
        patient_id=request.patient_id,
        status=status,
        priority=final,
        recommended_action=rules.escalation.action_for(final),
        rule_priority=floor,
        red_flag_priority=floor_from(hits),
        ai_escalated=escalated,
        news2=news2,
        red_flags=hits,
        reasons=reasons,
        concerning_findings=_dedupe(concerning),
        missing_information=_dedupe(missing),
        data_quality_issues=report.descriptions(),
        context_factors=interpretation.context_factors,
        requires_human_review=True,
        interpretation=interpretation,
        ruleset_version=rules.ruleset_version,
        engine_version=ENGINE_VERSION,
        model=interpretation.model,
        latency_ms=latency_ms,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out
