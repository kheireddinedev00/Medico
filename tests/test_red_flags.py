"""The red-flag matcher, and what it does with a negative.

Two failure modes are being guarded against, and they pull in opposite directions:

- Missing a real red flag, which leaves a sick patient in the waiting room.
- Firing on a negated one, which sends everybody to CRITICAL and teaches the staff to
  ignore the system - after which it might as well not exist.

The matcher is deliberately biased towards the first: where negation is ambiguous, the
flag fires.
"""

from __future__ import annotations

import pytest

from triage.red_flags import detect, floor_from, is_negated, normalise
from triage.rules import get_rules
from triage.schema import Priority, TriageRequest


@pytest.fixture(scope="module")
def rules():
    return get_rules()


def flags(rules, complaint=None, symptoms=None, notes=None):
    request = TriageRequest(
        chief_complaint=complaint, symptoms=symptoms or [], notes=notes, age_years=40
    )
    return detect(request, rules)


def ids(hits) -> set[str]:
    return {hit.id for hit in hits}


# --------------------------------------------------------------------------------
# Firing
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "complaint,flag_id",
    [
        ("crushing chest pain radiating to the arm", "cardiac_chest_pain"),
        ("sudden slurred speech since this morning", "stroke_symptoms"),
        ("stridor and drooling", "airway_compromise"),
        ("swollen throat after a bee sting", "anaphylaxis"),
        ("vomiting blood twice tonight", "severe_haemorrhage"),
        ("found unresponsive at home", "altered_consciousness"),
        ("non-blanching rash and rigors", "sepsis_pattern"),
        ("took too many tablets", "self_harm_overdose"),
        ("bleeding in pregnancy", "obstetric_emergency"),
        ("hit by a car this afternoon", "major_trauma"),
        ("worst headache of my life", "meningitis_pattern"),
        ("severe abdominal pain since noon", "acute_abdomen"),
        ("cannot pass urine and saddle numbness", "cauda_equina"),
        ("sudden vision loss in one eye", "acute_vision_loss"),
        ("cannot finish a sentence", "severe_breathlessness"),
    ],
)
def test_representative_presentations_fire(rules, complaint, flag_id):
    assert flag_id in ids(flags(rules, complaint))


def test_a_flag_in_the_symptoms_list_fires(rules):
    assert "cardiac_chest_pain" in ids(flags(rules, "unwell", symptoms=["chest tightness"]))


def test_a_flag_in_the_notes_fires(rules):
    assert "major_trauma" in ids(
        flags(rules, "leg pain", notes="fell from height at work this morning")
    )


def test_matching_is_case_and_punctuation_insensitive(rules):
    assert "cardiac_chest_pain" in ids(flags(rules, "CHEST   PAIN, severe!"))


def test_several_flags_can_fire_at_once(rules):
    hits = flags(rules, "chest pain and cannot breathe, feeling faint")
    assert len(hits) >= 2
    assert hits[0].floor == Priority.CRITICAL


def test_hits_are_ordered_most_urgent_first(rules):
    hits = flags(rules, "severe abdominal pain and vomiting blood")
    assert [h.floor for h in hits] == sorted(
        [h.floor for h in hits],
        key=lambda p: {Priority.CRITICAL: 0, Priority.URGENT: 1,
                       Priority.STANDARD: 2, Priority.LOW: 3}[p],
    )


# --------------------------------------------------------------------------------
# Not firing
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "complaint",
    [
        "sore throat, no chest pain",
        "cough, denies chest pain",
        "headache without neck stiffness",
        "no significant chest pain",
        "sore throat for three days, no chest pain and no breathlessness",
    ],
)
def test_negated_complaints_do_not_fire(rules, complaint):
    assert flags(rules, complaint) == []


def test_known_limitation_negation_after_the_phrase_still_fires(rules):
    """Documented, not accidental.

    The matcher only looks BEHIND a phrase for a negation cue, so "chest pain ruled out
    last week" fires the cardiac flag. That is over-triage - the safe direction - and
    fixing it properly needs clause parsing rather than a wider window, which would
    reintroduce the failure the window was narrowed to fix. The AI layer sees the same
    text and can note it in context_factors; it cannot lower the priority, so a
    clinician resolves it. This test exists so the behaviour is a decision rather than a
    surprise.
    """
    assert "cardiac_chest_pain" in ids(flags(rules, "review after chest pain ruled out last week"))


def test_a_negation_elsewhere_does_not_suppress_a_real_flag(rules):
    """'No fever' must not silence the chest pain in the same sentence."""
    assert "cardiac_chest_pain" in ids(flags(rules, "no fever but severe chest pain"))


def test_an_ordinary_complaint_fires_nothing(rules):
    assert flags(rules, "repeat prescription for blood pressure tablets") == []
    assert flags(rules, "small cut on finger, bleeding stopped") == []


def test_no_complaint_at_all_fires_nothing(rules):
    assert flags(rules, None) == []


def test_negation_window_is_short_enough_to_scope(rules):
    text = normalise("denies alcohol use and any recent travel, reports severe chest pain")
    position = text.find("chest pain")
    assert is_negated(text, position, ["denies "], window=15) is False


def test_negation_window_catches_an_adjacent_cue(rules):
    text = normalise("no chest pain")
    assert is_negated(text, text.find("chest pain"), ["no "], window=15) is True


def test_word_boundary_is_respected_for_negation_cues(rules):
    """'no ' must not match inside 'nose'."""
    assert "cardiac_chest_pain" in ids(flags(rules, "blocked nose and chest pain"))


# --------------------------------------------------------------------------------
# Context-dependent flags
# --------------------------------------------------------------------------------


def test_context_flag_needs_both_halves(rules):
    """Chemotherapy alone is a history; chemotherapy with a fever is an emergency."""
    assert "immunosuppressed_fever" not in ids(flags(rules, "on chemotherapy, needs a sick note"))
    assert "immunosuppressed_fever" in ids(
        flags(rules, "fever and chills", notes="started chemotherapy two weeks ago")
    )


def test_context_flag_reports_both_matched_phrases(rules):
    hit = next(
        h for h in flags(rules, "fever", notes="on chemotherapy")
        if h.id == "immunosuppressed_fever"
    )
    assert "+" in hit.matched


# --------------------------------------------------------------------------------
# Floors
# --------------------------------------------------------------------------------


def test_floor_is_the_most_urgent_flag(rules):
    assert floor_from(flags(rules, "severe abdominal pain and chest pain")) == Priority.CRITICAL


def test_floor_of_an_urgent_only_presentation(rules):
    assert floor_from(flags(rules, "worst headache of my life")) == Priority.URGENT


def test_no_flags_means_no_floor(rules):
    assert floor_from([]) is None


def test_every_hit_carries_a_reason_and_what_matched(rules):
    for hit in flags(rules, "crushing chest pain and slurred speech"):
        assert hit.reason
        assert hit.matched
        assert hit.detected_by == "rules"
