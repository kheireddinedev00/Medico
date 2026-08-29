"""The waiting room: ordering, staleness, deterioration, and clinician override.

The behaviours tested here are the ones a nurse will notice immediately if they are
wrong - a queue that reshuffles under them, a patient who appears twice after being
re-triaged, or an override that gets silently discarded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from triage.queue import WaitingRoom
from triage.rules import get_rules
from triage.schema import Priority, TriageRequest, TriageVitals
from triage.service import retriage, triage


@pytest.fixture(scope="module")
def rules():
    return get_rules()


NORMAL = {
    "temperature_c": 36.8, "heart_rate": 76, "respiratory_rate": 16,
    "blood_pressure": "122/78", "spo2": 98, "on_oxygen": False, "consciousness": "alert",
}


def at(minutes: int) -> datetime:
    base = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


def triaged(rules, complaint="cough", name=None, **vitals):
    request = TriageRequest(
        age_years=40, display_name=name, chief_complaint=complaint,
        vitals=TriageVitals(**{**NORMAL, **vitals}),
    )
    return triage(request, rules=rules, rules_only=True)


# --------------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------------


def test_queue_is_ordered_by_priority(rules):
    room = WaitingRoom()
    room.admit(triaged(rules, "cough", name="low"), display_name="low", arrived_at=at(0))
    room.admit(
        triaged(rules, "crushing chest pain", name="critical"),
        display_name="critical", arrived_at=at(30),
    )
    room.admit(
        triaged(rules, "worst headache of my life", name="urgent"),
        display_name="urgent", arrived_at=at(15),
    )

    assert [e.display_name for e in room.ordered(rules)] == ["critical", "urgent", "low"]


def test_same_priority_is_first_come_first_served(rules):
    room = WaitingRoom()
    room.admit(triaged(rules), display_name="second", arrived_at=at(20))
    room.admit(triaged(rules), display_name="first", arrived_at=at(5))
    room.admit(triaged(rules), display_name="third", arrived_at=at(45))

    assert [e.display_name for e in room.ordered(rules)] == ["first", "second", "third"]


def test_ordering_is_total_and_stable(rules):
    """Identical priority and identical arrival time must still order deterministically."""
    room = WaitingRoom()
    for index in range(5):
        room.admit(triaged(rules), display_name=f"p{index}", arrived_at=at(0))

    first = [e.result.request_id for e in room.ordered(rules)]
    assert first == [e.result.request_id for e in room.ordered(rules)]


def test_next_patient_is_the_head_of_the_queue(rules):
    room = WaitingRoom()
    room.admit(triaged(rules), display_name="waiting", arrived_at=at(0))
    room.admit(triaged(rules, "stridor and drooling"), display_name="airway", arrived_at=at(40))

    assert room.next_patient(rules).display_name == "airway"


def test_an_empty_room_has_no_next_patient(rules):
    assert WaitingRoom().next_patient(rules) is None


def test_counts_by_priority(rules):
    room = WaitingRoom()
    room.admit(triaged(rules))
    room.admit(triaged(rules))
    room.admit(triaged(rules, "crushing chest pain"))

    counts = room.counts()
    assert counts[Priority.LOW] == 2
    assert counts[Priority.CRITICAL] == 1


# --------------------------------------------------------------------------------
# Removal
# --------------------------------------------------------------------------------


def test_a_removed_patient_leaves_the_queue_but_stays_in_the_record(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), display_name="seen")
    key = entry.result.request_id

    room.remove(key, reason="seen by clinician")

    assert len(room) == 0
    assert room.entries[key].removal_reason == "seen by clinician"
    assert room.entries[key].removed_at is not None


def test_removing_an_unknown_key_is_not_an_error(rules):
    assert WaitingRoom().remove("nope") is None


# --------------------------------------------------------------------------------
# Re-triage and deterioration
# --------------------------------------------------------------------------------


def test_retriage_updates_in_place_rather_than_adding_a_second_entry(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), display_name="patient", arrived_at=at(0))
    key = entry.result.request_id

    worse = triaged(rules, "cough", respiratory_rate=30, spo2=88)
    room.update(key, worse)

    assert len(room) == 1
    assert room.entries[key].priority == worse.priority
    assert len(room.entries[key].history) == 2


def test_arrival_time_survives_a_retriage(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), arrived_at=at(0))
    key = entry.result.request_id

    room.update(key, triaged(rules, "cough", heart_rate=115))

    assert room.entries[key].arrived_at == at(0)


def test_deterioration_is_visible_on_the_entry(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), arrived_at=at(0))
    room.update(entry.result.request_id, triaged(rules, "cough", spo2=88, respiratory_rate=28))

    assert entry.deteriorating() is True


def test_a_stable_patient_is_not_marked_deteriorating(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), arrived_at=at(0))
    room.update(entry.result.request_id, triaged(rules))

    assert entry.deteriorating() is False


def test_retriage_notes_the_deterioration_for_the_nurse(rules):
    first = triaged(rules)
    request = TriageRequest(
        age_years=40, chief_complaint="cough",
        vitals=TriageVitals(**{**NORMAL, "spo2": 86, "respiratory_rate": 30}),
    )
    second = retriage(first, request, rules=rules, rules_only=True)

    assert "DETERIORATED" in second.reasons[0]
    assert any("risen" in f.lower() for f in second.concerning_findings)


def test_retriage_allows_improvement_but_asks_for_confirmation(rules):
    worse = triaged(rules, "cough", spo2=86, respiratory_rate=30)
    request = TriageRequest(
        age_years=40, chief_complaint="cough", vitals=TriageVitals(**NORMAL)
    )
    better = retriage(worse, request, rules=rules, rules_only=True)

    assert better.priority == Priority.LOW
    assert "Improved" in better.reasons[0]


def test_retriage_does_not_anchor_on_the_previous_result(rules):
    """A patient who is now well is scored as well, not held at their arrival priority."""
    worse = triaged(rules, "cough", spo2=86, respiratory_rate=30)
    request = TriageRequest(
        age_years=40, chief_complaint="cough", vitals=TriageVitals(**NORMAL)
    )
    better = retriage(worse, request, rules=rules, rules_only=True)

    assert better.rule_priority == Priority.LOW


# --------------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------------


def test_a_standard_patient_goes_stale_after_the_configured_interval(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules, "sore throat", temperature_c=38.2), arrived_at=at(0))
    entry.triaged_at = at(0)

    interval = rules.waiting_room.interval_for(entry.priority)
    assert entry.needs_retriage(rules, now=at(interval - 1)) is False
    assert entry.needs_retriage(rules, now=at(interval + 1)) is True


def test_a_critical_patient_is_always_flagged(rules):
    """A CRITICAL patient should not be in the waiting room at all."""
    room = WaitingRoom()
    entry = room.admit(triaged(rules, "crushing chest pain"), arrived_at=at(0))
    entry.triaged_at = at(0)

    assert entry.needs_retriage(rules, now=at(0)) is True


def test_stale_returns_everyone_due_for_re_measurement(rules):
    room = WaitingRoom()
    fresh = room.admit(triaged(rules), arrived_at=at(0))
    fresh.triaged_at = at(115)
    old = room.admit(triaged(rules), arrived_at=at(0))
    old.triaged_at = at(0)

    stale = room.stale(rules, now=at(120))
    assert old in stale and fresh not in stale


def test_waiting_minutes(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), arrived_at=at(0))
    assert entry.waiting_minutes(now=at(45)) == pytest.approx(45)


# --------------------------------------------------------------------------------
# Clinician override
# --------------------------------------------------------------------------------


def test_an_override_changes_the_queue_position(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules), display_name="patient", arrived_at=at(0))
    key = entry.result.request_id

    room.override(key, Priority.URGENT, reason="looks unwell at the desk", clinician="Dr A")

    assert entry.priority == Priority.URGENT
    assert room.next_patient(rules).display_name == "patient"


def test_an_override_never_overwrites_what_the_system_said(rules):
    """The disagreement has to stay answerable from the record."""
    room = WaitingRoom()
    entry = room.admit(triaged(rules, "crushing chest pain"))
    key = entry.result.request_id

    room.override(key, Priority.STANDARD, reason="known chronic chest wall pain", clinician="Dr B")

    assert entry.result.priority == Priority.CRITICAL       # what the agent decided
    assert entry.result.override.priority == Priority.STANDARD  # what the clinician decided
    assert entry.priority == Priority.STANDARD               # what the queue sorts on
    assert entry.result.override.clinician == "Dr B"


def test_an_override_requires_a_reason(rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules))

    with pytest.raises(ValueError, match="needs a reason"):
        room.override(entry.result.request_id, Priority.LOW, reason="   ")


def test_overriding_an_unknown_entry_raises(rules):
    with pytest.raises(KeyError):
        WaitingRoom().override("nope", Priority.LOW, reason="because")
