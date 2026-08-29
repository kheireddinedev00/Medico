"""The scoring engine, checked at the band boundaries.

Boundaries are where a transcription error hides: a table written with 21-24 instead of
21-25 looks right and misscores exactly one value. Every parameter is therefore tested
at the first and last value of each band rather than in the middle.

If any of these fail after someone edits `data/triage_rules.json`, the edit changed the
scale - which may be intended, but is never accidental.
"""

from __future__ import annotations

import pytest

from triage.news2 import EXPECTED_PARAMETERS, describe, score
from triage.rules import Band, BandedParameter, _check_bands, get_rules
from triage.schema import NormalisedVitals


@pytest.fixture(scope="module")
def rules():
    return get_rules()


def vitals(**kwargs) -> NormalisedVitals:
    return NormalisedVitals(**kwargs)


def score_of(rules, parameter: str, **kwargs) -> int:
    result = score(vitals(**kwargs), rules)
    match = next(p for p in result.parameters if p.parameter == parameter)
    assert match.scored, f"{parameter} was not scored"
    return match.score


# --------------------------------------------------------------------------------
# Parameter tables, at their boundaries
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rate,expected",
    [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3), (40, 3)],
)
def test_respiration_rate_bands(rules, rate, expected):
    assert score_of(rules, "respiration_rate", respiratory_rate=rate) == expected


@pytest.mark.parametrize(
    "saturation,expected",
    [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)],
)
def test_spo2_scale_1_bands(rules, saturation, expected):
    assert score_of(rules, "spo2", spo2=saturation) == expected


@pytest.mark.parametrize(
    "systolic,expected",
    [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)],
)
def test_systolic_bands(rules, systolic, expected):
    assert score_of(rules, "systolic_bp", systolic_bp=systolic) == expected


@pytest.mark.parametrize(
    "pulse,expected",
    [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)],
)
def test_pulse_bands(rules, pulse, expected):
    assert score_of(rules, "pulse", heart_rate=pulse) == expected


@pytest.mark.parametrize(
    "temperature,expected",
    [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0), (38.1, 1), (39.0, 1), (39.1, 2)],
)
def test_temperature_bands(rules, temperature, expected):
    assert score_of(rules, "temperature", temperature_c=temperature) == expected


@pytest.mark.parametrize(
    "level,expected",
    [("alert", 0), ("confusion", 3), ("voice", 3), ("pain", 3), ("unresponsive", 3)],
)
def test_consciousness_scores(rules, level, expected):
    assert score_of(rules, "consciousness", consciousness=level) == expected


def test_new_confusion_scores_the_same_as_reduced_consciousness(rules):
    """NEWS2 added the C to AVPU precisely because delirium was being missed."""
    assert score_of(rules, "consciousness", consciousness="confusion") == score_of(
        rules, "consciousness", consciousness="unresponsive"
    )


def test_supplemental_oxygen_is_scored_in_its_own_right(rules):
    assert score_of(rules, "air_or_oxygen", on_oxygen=True) == 2
    assert score_of(rules, "air_or_oxygen", on_oxygen=False) == 0


# --------------------------------------------------------------------------------
# Scale 2
# --------------------------------------------------------------------------------


def test_scale_2_treats_the_target_range_as_normal(rules):
    """90% on oxygen is at target for a Scale 2 patient and hypoxic for anyone else."""
    observations = vitals(spo2=90, on_oxygen=True)

    scale_1 = score(observations, rules, hypercapnic_target_range=False)
    scale_2 = score(observations, rules, hypercapnic_target_range=True)

    assert scale_1.aggregate == 5  # saturation 3 + oxygen 2
    assert scale_2.aggregate == 2  # saturation 0 + oxygen 2
    assert scale_1.scale == 1 and scale_2.scale == 2


def test_scale_2_penalises_over_oxygenation(rules):
    """Above the target range, on oxygen, is itself the danger in this group."""
    high = score(vitals(spo2=98, on_oxygen=True), rules, hypercapnic_target_range=True)
    parameter = next(p for p in high.parameters if p.parameter == "spo2")
    assert parameter.score == 3


def test_scale_2_on_air_does_not_penalise_a_high_saturation(rules):
    result = score(vitals(spo2=98, on_oxygen=False), rules, hypercapnic_target_range=True)
    parameter = next(p for p in result.parameters if p.parameter == "spo2")
    assert parameter.score == 0


# --------------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------------


def test_missing_observations_score_nothing_rather_than_zero(rules):
    result = score(vitals(respiratory_rate=16), rules)

    assert result.aggregate == 0
    assert result.scored_count == 1
    assert result.complete() is False
    unscored = [p for p in result.parameters if not p.scored]
    assert len(unscored) == EXPECTED_PARAMETERS - 1
    assert all(p.score is None for p in unscored)


def test_unrecorded_oxygen_is_not_assumed_to_be_air(rules):
    result = score(vitals(spo2=97, on_oxygen=None), rules)
    parameter = next(p for p in result.parameters if p.parameter == "air_or_oxygen")
    assert parameter.scored is False


def test_a_full_set_is_complete(rules):
    result = score(
        vitals(
            respiratory_rate=16, spo2=98, on_oxygen=False, systolic_bp=120,
            heart_rate=76, consciousness="alert", temperature_c=36.8,
        ),
        rules,
    )
    assert result.complete() is True
    assert result.aggregate == 0
    assert result.abnormal() == []


def test_describe_reports_what_was_not_measured(rules):
    lines = describe(score(vitals(respiratory_rate=30), rules))
    assert any("not recorded" in line for line in lines)


# --------------------------------------------------------------------------------
# The red-score rule
# --------------------------------------------------------------------------------


def test_single_parameter_red_is_detected(rules):
    result = score(vitals(respiratory_rate=26, heart_rate=70, temperature_c=37.0), rules)
    assert result.single_parameter_red is True
    assert "Respiration rate" in result.red_parameters


def test_no_red_when_every_parameter_scores_below_three(rules):
    result = score(vitals(respiratory_rate=22, heart_rate=95, temperature_c=38.5), rules)
    assert result.aggregate == 4
    assert result.single_parameter_red is False


# --------------------------------------------------------------------------------
# The rule file's own integrity
# --------------------------------------------------------------------------------


def test_shipped_tables_have_no_gaps_or_overlaps(rules):
    for name in ("respiration_rate", "spo2_scale_1", "systolic_bp", "pulse", "temperature"):
        assert _check_bands(name, getattr(rules.news2.parameters, name)) == []


def test_overlapping_bands_are_rejected():
    overlapping = BandedParameter(
        label="x", unit="u",
        bands=[Band(min=None, max=10, score=0), Band(min=10, max=None, score=1)],
    )
    problems = _check_bands("x", overlapping)
    assert any("overlap" in p for p in problems)


def test_gaps_between_bands_are_rejected():
    gapped = BandedParameter(
        label="x", unit="u",
        bands=[Band(min=None, max=10, score=0), Band(min=20, max=None, score=1)],
    )
    problems = _check_bands("x", gapped)
    assert any("gap" in p for p in problems)
