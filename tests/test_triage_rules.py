"""Loading the rule set, and refusing to run on a broken one.

The agent has no degraded mode for rules: a malformed threshold file stops it. These
tests are the evidence that it really does stop, rather than falling back on something
nobody reviewed - which is the failure mode that would be invisible in production and
catastrophic in a triage queue.
"""

from __future__ import annotations

import json

import pytest

from triage.rules import (
    ENGINE_VERSION,
    RuleSetError,
    check_consistency,
    get_rules,
    load_rules,
)
from triage.schema import Priority


@pytest.fixture(scope="module")
def rules():
    return get_rules()


@pytest.fixture
def rule_file(tmp_path):
    """A copy of the shipped rule set that a test may corrupt."""
    from config import TRIAGE_RULES

    def write(mutate=None):
        raw = json.loads(TRIAGE_RULES.read_text(encoding="utf-8"))
        if mutate:
            mutate(raw)
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    return write


# --------------------------------------------------------------------------------
# The shipped file
# --------------------------------------------------------------------------------


def test_shipped_rule_set_loads_and_is_consistent(rules):
    assert rules.ruleset_version
    assert check_consistency(rules) == []


def test_every_clinical_number_lives_in_the_rule_file(rules):
    """The engine holds no thresholds of its own - spot-checked against the file."""
    parameters = rules.news2.parameters
    assert parameters.respiration_rate.bands
    assert parameters.consciousness.values["alert"] == 0
    assert rules.escalation.single_parameter_floor.trigger_score == 3
    assert rules.population.min_age_years == 16


def test_the_source_is_named_and_citable(rules):
    source = rules.news2.source
    assert source.publisher and source.year and source.title
    assert "Royal College of Physicians" in source.citation()


def test_verification_status_is_honest(rules):
    """Until a human checks the tables against the RCP chart, the file must say so.

    This test will fail the day someone marks the file VERIFIED, which is the point:
    the change should be deliberate and reviewed, not a passing edit.
    """
    assert rules.is_verified() is False
    assert "TRANSCRIBED" in rules.verification_status


def test_all_four_priorities_have_a_recommended_action(rules):
    for priority in Priority:
        action = rules.escalation.action_for(priority)
        assert action and action != "Clinical assessment required."


def test_engine_version_is_stamped():
    assert ENGINE_VERSION


# --------------------------------------------------------------------------------
# Refusing broken rule sets
# --------------------------------------------------------------------------------


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(RuleSetError, match="No triage rule set"):
        load_rules(tmp_path / "does_not_exist.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuleSetError, match="not valid JSON"):
        load_rules(path)


def test_a_missing_parameter_raises(rule_file):
    def drop_pulse(raw):
        del raw["news2"]["parameters"]["pulse"]

    with pytest.raises(RuleSetError, match="does not match the expected format"):
        load_rules(rule_file(drop_pulse))


def test_an_unknown_priority_raises(rule_file):
    def typo(raw):
        raw["red_flags"]["flags"][0]["floor"] = "CRITCAL"

    with pytest.raises(RuleSetError, match="does not match the expected format"):
        load_rules(rule_file(typo))


def test_overlapping_bands_are_refused(rule_file):
    def overlap(raw):
        raw["news2"]["parameters"]["pulse"]["bands"][1]["min"] = 40

    with pytest.raises(RuleSetError, match="overlap"):
        load_rules(rule_file(overlap))


def test_a_gap_between_bands_is_refused(rule_file):
    def gap(raw):
        raw["news2"]["parameters"]["pulse"]["bands"][1]["min"] = 45

    with pytest.raises(RuleSetError, match="gap"):
        load_rules(rule_file(gap))


def test_an_escalation_gap_is_refused(rule_file):
    def gap(raw):
        raw["escalation"]["bands"][1]["max_aggregate"] = 3

    with pytest.raises(RuleSetError, match="not covered exactly once"):
        load_rules(rule_file(gap))


def test_duplicate_red_flag_ids_are_refused(rule_file):
    def duplicate(raw):
        raw["red_flags"]["flags"][1]["id"] = raw["red_flags"]["flags"][0]["id"]

    with pytest.raises(RuleSetError, match="more than once"):
        load_rules(rule_file(duplicate))


def test_a_required_field_that_does_not_exist_is_refused(rule_file):
    """A typo here would mean the field is silently never reported as missing."""
    def typo(raw):
        raw["validation"]["required_for_full_score"].append("pulse_rate")

    with pytest.raises(RuleSetError, match="plausible range"):
        load_rules(rule_file(typo))


def test_an_empty_version_is_refused(rule_file):
    def blank(raw):
        raw["ruleset_version"] = "  "

    with pytest.raises(RuleSetError):
        load_rules(rule_file(blank))


# --------------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------------


def test_underscore_keys_are_treated_as_comments(rule_file):
    def annotate(raw):
        raw["_editor_note"] = "reviewed by nobody"
        raw["news2"]["parameters"]["pulse"]["_note"] = "checked 2026-08-01"

    assert load_rules(rule_file(annotate)).ruleset_version
