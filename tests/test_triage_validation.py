"""Validation and normalisation.

The property under test throughout: **nothing is invented**. A missing value stays
missing, an unreadable value is reported rather than guessed at, and an implausible one
is dropped rather than scored. Every test here is a way of asking whether some path
through the code quietly produces a number nobody measured.
"""

from __future__ import annotations

import pytest

from triage.rules import get_rules
from triage.schema import TriageRequest, TriageVitals
from triage.validation import parse_blood_pressure, validate


@pytest.fixture(scope="module")
def rules():
    return get_rules()


def check(rules, age=40, **vitals):
    request = TriageRequest(
        age_years=age, chief_complaint="cough", vitals=TriageVitals(**vitals)
    )
    return validate(request, rules)


def issues_for(report, field):
    return [i for i in report.issues if i.field == field]


# --------------------------------------------------------------------------------
# Blood pressure
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("128/76", (128, 76)),
        ("128 / 76", (128, 76)),
        ("128/76 mmHg", (128, 76)),
        ("128/76 (left arm)", (128, 76)),
        ("90/60", (90, 60)),
    ],
)
def test_blood_pressure_forms_a_chart_actually_carries(text, expected):
    assert parse_blood_pressure(text) == expected


@pytest.mark.parametrize("text", ["see chart", "", "normal", "one twenty over eighty"])
def test_unreadable_blood_pressure_returns_none(text):
    assert parse_blood_pressure(text) is None


def test_unparseable_blood_pressure_is_reported_not_guessed(rules):
    report = check(rules, blood_pressure="see chart")

    assert report.normalised.systolic_bp is None
    problems = issues_for(report, "blood_pressure")
    assert problems and problems[0].issue == "unparseable"


def test_structured_blood_pressure_wins_over_the_string(rules):
    report = check(rules, blood_pressure="128/76", systolic_bp=90, diastolic_bp=60)
    assert report.normalised.systolic_bp == 90


def test_transposed_blood_pressure_is_flagged_but_still_scored(rules):
    """The reading is not silently swapped - the nurse is told, and 80 is scored as 80."""
    report = check(rules, blood_pressure="80/120")

    assert report.normalised.systolic_bp == 80
    assert any(i.issue == "contradictory" for i in issues_for(report, "blood_pressure"))


# --------------------------------------------------------------------------------
# Implausible values
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [("heart_rate", 950), ("respiratory_rate", 200), ("spo2", 5), ("temperature_c", 60.0)],
)
def test_implausible_values_are_dropped_and_reported(rules, field, value):
    report = check(rules, **{field: value})

    assert getattr(report.normalised, field) is None
    problems = issues_for(report, field)
    assert problems and problems[0].issue == "implausible"
    assert problems[0].severity == "error"


def test_an_extreme_but_survivable_value_is_kept(rules):
    """The plausible range catches typing errors, not sick patients."""
    report = check(rules, heart_rate=185, spo2=62)

    assert report.normalised.heart_rate == 185
    assert report.normalised.spo2 == 62


# --------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------


def test_fahrenheit_in_a_celsius_field_is_dropped_not_converted(rules):
    """99.1 scored as Celsius is a fever the patient does not have."""
    report = check(rules, temperature_c=99.1)

    assert report.normalised.temperature_c is None
    problems = issues_for(report, "temperature_c")
    assert problems and problems[0].issue == "unit_suspect"
    assert "Fahrenheit" in problems[0].detail


def test_a_normal_celsius_temperature_is_untouched(rules):
    assert check(rules, temperature_c=37.4).normalised.temperature_c == 37.4


def test_suspicious_weight_is_flagged_but_kept(rules):
    report = check(rules, weight_kg=240)

    assert report.normalised.weight_kg == 240
    problems = issues_for(report, "weight_kg")
    assert problems and problems[0].severity == "warning"


# --------------------------------------------------------------------------------
# Missing information
# --------------------------------------------------------------------------------


def test_missing_observations_are_listed_not_filled_in(rules):
    report = check(rules, temperature_c=37.2)

    assert set(report.missing_required) == {
        "respiratory_rate", "spo2", "systolic_bp", "heart_rate", "consciousness"
    }
    assert report.normalised.heart_rate is None
    assert report.normalised.spo2 is None


def test_a_complete_set_reports_nothing_missing(rules):
    report = check(
        rules, temperature_c=36.8, heart_rate=76, respiratory_rate=16,
        blood_pressure="122/78", spo2=98, on_oxygen=False, consciousness="alert",
    )
    assert report.missing_required == []
    assert report.errors() == []


def test_unknown_consciousness_counts_as_missing(rules):
    report = check(rules, consciousness="unknown")
    assert "consciousness" in report.missing_required


def test_unrecorded_oxygen_is_reported_when_a_saturation_exists(rules):
    report = check(rules, spo2=97, on_oxygen=None)
    assert issues_for(report, "on_oxygen")


def test_missing_chief_complaint_is_reported(rules):
    request = TriageRequest(age_years=40, vitals=TriageVitals(spo2=98))
    report = validate(request, rules)
    assert any(i.field == "chief_complaint" for i in report.issues)


def test_an_implausible_value_is_not_reported_twice(rules):
    """Dropped for being impossible, then missing. The nurse gets one message, not two."""
    report = check(rules, heart_rate=950)
    assert len(issues_for(report, "heart_rate")) == 1


# --------------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------------


def test_a_child_is_out_of_scope(rules):
    report = check(rules, age=7)
    assert report.out_of_scope is True
    assert "paediatric" in report.out_of_scope_reason


def test_an_unrecorded_age_is_not_out_of_scope(rules):
    """Most adults at a front desk have no age on the form; refusing them all is useless."""
    request = TriageRequest(chief_complaint="cough", vitals=TriageVitals(spo2=98))
    report = validate(request, rules)
    assert report.out_of_scope is False


def test_sixteen_is_inside_the_validated_range(rules):
    assert check(rules, age=16).out_of_scope is False
