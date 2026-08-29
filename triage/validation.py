"""Turning what was typed into what can be scored - and saying what was wrong with it.

This is the first stage of the pipeline and the only one allowed to interpret raw input.
Everything after it reads `NormalisedVitals`, where each field is either a usable number
or None.

The rule the whole module is built around: **nothing is invented**. A missing
respiratory rate stays missing. It is not imputed from the heart rate, not defaulted to
a normal value, not carried over from a previous set of observations. A normal value
substituted for a missing one is indistinguishable, downstream, from a normal value that
was actually measured - and the second one means the patient is fine while the first
means nobody looked.

Four kinds of problem are separated because they need different responses:

- **missing** - nobody recorded it. Report it; score what remains.
- **unparseable** - something was recorded but cannot be read ("BP: see chart").
- **implausible** - outside any survivable range. Dropped, because a heart rate of 950
  is a keyboard, not a patient.
- **unit_suspect** - inside the plausible range for a different unit. Kept and flagged,
  because 99 could be a fever in Fahrenheit or a normal pulse, and only a person can
  tell which.
"""

from __future__ import annotations

import re
from typing import Optional

from triage.rules import RuleSet
from triage.schema import (
    DataIssue,
    NormalisedVitals,
    TriageRequest,
    ValidationReport,
)

# "128/76", "128 / 76", "128/76 mmHg", "128/76 (left arm)". Anything else is unparseable
# on purpose - a looser pattern starts reading "1 in 28/76" as a blood pressure.
_BP_RE = re.compile(r"(?P<systolic>\d{2,3})\s*/\s*(?P<diastolic>\d{2,3})")

_FIELD_LABELS = {
    "temperature_c": "Temperature",
    "heart_rate": "Heart rate",
    "respiratory_rate": "Respiratory rate",
    "systolic_bp": "Systolic blood pressure",
    "diastolic_bp": "Diastolic blood pressure",
    "spo2": "Oxygen saturation",
    "consciousness": "Level of consciousness",
    "weight_kg": "Weight",
    "height_cm": "Height",
}


def label_for(field: str) -> str:
    return _FIELD_LABELS.get(field, field)


def parse_blood_pressure(text: str) -> Optional[tuple[int, int]]:
    """Recover systolic and diastolic from a chart string, or None."""
    match = _BP_RE.search(text)
    if not match:
        return None
    return int(match.group("systolic")), int(match.group("diastolic"))


def _plausible(
    rules: RuleSet, field: str, value: float, issues: list[DataIssue]
) -> Optional[float]:
    """Keep the value, or drop it and say why."""
    allowed = rules.validation.plausible.get(field)
    if allowed is None or allowed.contains(value):
        return value
    issues.append(
        DataIssue(
            field=field,
            issue="implausible",
            severity="error",
            detail=(
                f"{value:g} is outside the plausible range "
                f"{allowed.min:g}-{allowed.max:g}; treated as not recorded"
            ),
        )
    )
    return None


def _temperature(rules: RuleSet, value: float, issues: list[DataIssue]) -> Optional[float]:
    """Temperature, checked for Fahrenheit BEFORE plausibility.

    Order matters here. A Fahrenheit reading is also outside the plausible Celsius
    range, so the generic check would catch it first and tell the nurse the value is
    impossible - true, but useless. The specific message says what to do about it.

    Dropped rather than merely flagged, and dropped rather than converted: 99 degrees
    scored as Celsius is a NEWS2 point for a patient who is afebrile, and converting it
    silently would mean the record no longer says what was typed.
    """
    if rules.validation.unit_suspicion.temperature_fahrenheit_range.contains(value):
        issues.append(
            DataIssue(
                field="temperature_c",
                issue="unit_suspect",
                severity="error",
                detail=(
                    f"{value:g} looks like Fahrenheit, not Celsius. Not converted and "
                    f"not scored - re-enter it in Celsius."
                ),
            )
        )
        return None
    return _plausible(rules, "temperature_c", value, issues)


def _check_units(rules: RuleSet, vitals: NormalisedVitals, issues: list[DataIssue]) -> None:
    """Flag values that look like the wrong unit. Never converts them."""
    suspicion = rules.validation.unit_suspicion

    if vitals.weight_kg is not None and vitals.weight_kg >= suspicion.weight_pounds_min:
        issues.append(
            DataIssue(
                field="weight_kg",
                issue="unit_suspect",
                severity="warning",
                detail=f"{vitals.weight_kg:g} kg is possible but often means pounds were entered",
            )
        )

    if vitals.height_cm is not None and vitals.height_cm <= suspicion.height_inches_max:
        issues.append(
            DataIssue(
                field="height_cm",
                issue="unit_suspect",
                severity="warning",
                detail=f"{vitals.height_cm:g} cm is possible but often means inches were entered",
            )
        )


def _check_contradictions(vitals: NormalisedVitals, issues: list[DataIssue]) -> None:
    """Internal contradictions a person should look at before trusting the score."""
    if (
        vitals.systolic_bp is not None
        and vitals.diastolic_bp is not None
        and vitals.diastolic_bp >= vitals.systolic_bp
    ):
        issues.append(
            DataIssue(
                field="blood_pressure",
                issue="contradictory",
                severity="warning",
                detail=(
                    f"diastolic ({vitals.diastolic_bp}) is not below systolic "
                    f"({vitals.systolic_bp}); the reading may be transposed"
                ),
            )
        )

    if vitals.spo2 is not None and vitals.spo2 >= 97 and vitals.on_oxygen:
        issues.append(
            DataIssue(
                field="spo2",
                issue="contradictory",
                severity="warning",
                detail=(
                    "saturation is high while on supplemental oxygen - confirm whether "
                    "the patient needs a target range (NEWS2 Scale 2)"
                ),
            )
        )


def _check_scope(rules: RuleSet, request: TriageRequest, report: ValidationReport) -> None:
    """Refuse to score a patient the scale is not validated for.

    An unrecorded age is NOT out of scope. Most adults arriving at a desk have no age on
    the form, and refusing to triage all of them would make the agent useless; the
    missing age is reported instead and the adult scale is applied.
    """
    population = rules.population
    age = request.age_years

    if age is not None:
        if age < population.min_age_years:
            report.out_of_scope = True
            report.out_of_scope_reason = (
                f"Patient is {age}. This rule set is validated for ages "
                f"{population.min_age_years} and over - paediatric vital-sign thresholds "
                f"differ enough that scoring a child on the adult scale under-triages "
                f"them. Refer directly to a clinician."
            )
            return
        if population.max_age_years is not None and age > population.max_age_years:
            report.out_of_scope = True
            report.out_of_scope_reason = (
                f"Patient is {age}, above this rule set's validated range."
            )
            return

    if population.excludes_pregnancy and request.is_pregnant:
        report.out_of_scope = True
        report.out_of_scope_reason = (
            "Patient is recorded as pregnant. Pregnancy shifts the physiological "
            "normals this scale assumes (resting tachycardia, lower blood pressure), so "
            "it would systematically under-triage. Refer directly to a clinician."
        )


def validate(request: TriageRequest, rules: RuleSet) -> ValidationReport:
    """Normalise the request and report everything wrong with it."""
    issues: list[DataIssue] = []
    raw = request.vitals
    vitals = NormalisedVitals(consciousness=raw.consciousness)

    if raw.temperature_c is not None:
        vitals.temperature_c = _temperature(rules, raw.temperature_c, issues)
    if raw.heart_rate is not None:
        kept = _plausible(rules, "heart_rate", raw.heart_rate, issues)
        vitals.heart_rate = int(kept) if kept is not None else None
    if raw.respiratory_rate is not None:
        kept = _plausible(rules, "respiratory_rate", raw.respiratory_rate, issues)
        vitals.respiratory_rate = int(kept) if kept is not None else None
    if raw.spo2 is not None:
        vitals.spo2 = _plausible(rules, "spo2", raw.spo2, issues)
    if raw.weight_kg is not None:
        vitals.weight_kg = _plausible(rules, "weight_kg", raw.weight_kg, issues)
    if raw.height_cm is not None:
        vitals.height_cm = _plausible(rules, "height_cm", raw.height_cm, issues)

    # Blood pressure: structured wins, string is parsed, neither is invented.
    systolic, diastolic = raw.systolic_bp, raw.diastolic_bp
    if systolic is None and raw.blood_pressure:
        parsed = parse_blood_pressure(raw.blood_pressure)
        if parsed is None:
            issues.append(
                DataIssue(
                    field="blood_pressure",
                    issue="unparseable",
                    severity="error",
                    detail=(
                        f"could not read a systolic/diastolic pair from "
                        f"{raw.blood_pressure!r}; expected a form like '128/76'"
                    ),
                )
            )
        else:
            systolic, diastolic = parsed

    if systolic is not None:
        kept = _plausible(rules, "systolic_bp", systolic, issues)
        vitals.systolic_bp = int(kept) if kept is not None else None
    if diastolic is not None:
        kept = _plausible(rules, "diastolic_bp", diastolic, issues)
        vitals.diastolic_bp = int(kept) if kept is not None else None

    vitals.on_oxygen = raw.on_oxygen

    _check_units(rules, vitals, issues)
    _check_contradictions(vitals, issues)

    report = ValidationReport(normalised=vitals, issues=issues)

    # What is missing, after everything implausible has been dropped.
    for field in rules.validation.required_for_full_score:
        value = getattr(vitals, field, None)
        absent = value is None or (field == "consciousness" and value == "unknown")
        if absent:
            report.missing_required.append(field)
            already_reported = any(
                i.field == field and i.issue in {"implausible", "unit_suspect", "unparseable"}
                for i in issues
            )
            if not already_reported:
                report.issues.append(
                    DataIssue(
                        field=field,
                        issue="missing",
                        severity="warning",
                        detail="not recorded",
                    )
                )

    if raw.on_oxygen is None and vitals.spo2 is not None:
        report.issues.append(
            DataIssue(
                field="on_oxygen",
                issue="missing",
                severity="warning",
                detail=(
                    "not recorded whether the patient is on supplemental oxygen; the "
                    "oxygen parameter is not scored, so the total may understate risk"
                ),
            )
        )

    if not request.chief_complaint:
        report.issues.append(
            DataIssue(
                field="chief_complaint",
                issue="missing",
                severity="warning",
                detail=(
                    "no chief complaint recorded; presentational red flags cannot be "
                    "checked and this patient is being sorted on vital signs alone"
                ),
            )
        )

    _check_scope(rules, request, report)
    return report
