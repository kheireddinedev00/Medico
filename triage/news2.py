"""The NEWS2 aggregate, computed from the tables in the rule set.

Deliberately dull arithmetic. There is not a single clinical number in this file - every
threshold is read from `data/triage_rules.json`, and this module only looks values up
and adds them together. That is what lets a clinician review the thresholds without
reading Python, and what lets us change the scale without touching the engine.

Two decisions worth stating:

**A missing observation scores nothing rather than zero.** Zero is the score for a
*normal* value, and a patient who was never assessed is not a patient who is normal.
Missing parameters are counted and reported, so `News2Result.complete()` tells the
caller whether the aggregate means what it usually means.

**Scale 2 is opt-in and never inferred.** NEWS2 Scale 2 applies to patients with
confirmed hypercapnic respiratory failure and a prescribed 88-92% target. Guessing it
from a COPD diagnosis would be the single most dangerous inference in this codebase: a
COPD patient without a target range who desaturates to 89% scores 0 on Scale 2 and 3 on
Scale 1, and the second one is correct. So the caller has to say so explicitly, and
`triage/service.py` only sets it from an explicit field on the request.
"""

from __future__ import annotations

from typing import Optional

from triage.rules import BandedParameter, RuleSet, ValuedParameter
from triage.schema import News2Result, NormalisedVitals, ParameterScore

# Respiration, saturation, oxygen, blood pressure, pulse, consciousness, temperature.
EXPECTED_PARAMETERS = 7


def _banded(
    name: str,
    parameter: BandedParameter,
    value: Optional[float],
    suffix: str = "",
) -> ParameterScore:
    if value is None:
        return ParameterScore(parameter=name, label=parameter.label, scored=False)
    score = parameter.score_for(value)
    if score is None:
        # Only reachable if the rule set has a gap, which load_rules refuses to accept.
        return ParameterScore(
            parameter=name, label=parameter.label, value=f"{value:g}", scored=False
        )
    return ParameterScore(
        parameter=name,
        label=parameter.label,
        value=f"{value:g}{suffix}",
        score=score,
        scored=True,
    )


def _valued(name: str, parameter: ValuedParameter, value: Optional[str]) -> ParameterScore:
    if value is None:
        return ParameterScore(parameter=name, label=parameter.label, scored=False)
    score = parameter.score_for(value)
    if score is None:
        return ParameterScore(
            parameter=name, label=parameter.label, value=value, scored=False
        )
    return ParameterScore(
        parameter=name, label=parameter.label, value=value, score=score, scored=True
    )


def score(
    vitals: NormalisedVitals,
    rules: RuleSet,
    hypercapnic_target_range: bool = False,
) -> News2Result:
    """Score one set of observations. Pure: no I/O, no model, no clock."""
    scale = 2 if hypercapnic_target_range else 1
    tables = rules.news2.parameters

    parameters: list[ParameterScore] = [
        _banded("respiration_rate", tables.respiration_rate, vitals.respiratory_rate),
        _banded(
            "spo2",
            rules.spo2_parameter(scale, vitals.on_oxygen),
            vitals.spo2,
            suffix="%",
        ),
        _valued(
            "air_or_oxygen",
            tables.air_or_oxygen,
            None if vitals.on_oxygen is None else ("oxygen" if vitals.on_oxygen else "air"),
        ),
        _banded("systolic_bp", tables.systolic_bp, vitals.systolic_bp, suffix=" mmHg"),
        _banded("pulse", tables.pulse, vitals.heart_rate),
        _valued(
            "consciousness",
            tables.consciousness,
            None if vitals.consciousness == "unknown" else vitals.consciousness,
        ),
        _banded("temperature", tables.temperature, vitals.temperature_c, suffix="C"),
    ]

    scored = [p for p in parameters if p.scored]
    red = [p for p in scored if (p.score or 0) >= rules.escalation.single_parameter_floor.trigger_score]

    return News2Result(
        aggregate=sum(p.score or 0 for p in scored),
        parameters=parameters,
        single_parameter_red=bool(red),
        red_parameters=[p.label for p in red],
        scored_count=len(scored),
        expected_count=EXPECTED_PARAMETERS,
        scale=scale,
    )


def describe(result: News2Result) -> list[str]:
    """The abnormal parameters, phrased for a clinician reading the result."""
    lines = [p.describe() for p in result.abnormal()]
    if not result.complete():
        missing = [p.label for p in result.parameters if not p.scored]
        lines.append(
            f"Aggregate computed from {result.scored_count} of {result.expected_count} "
            f"parameters - not recorded: {', '.join(missing)}"
        )
    return lines
