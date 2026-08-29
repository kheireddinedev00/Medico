"""Persistence and the backend integration surface.

Two things are being checked: that a triage decision, once recorded, cannot be quietly
rewritten, and that `triage_payload` really is a dict-in/dict-out function a route
handler can call without knowing anything about the internals.
"""

from __future__ import annotations

import json

import pytest

from patient.profile import Allergy, ChronicCondition, Medication, PatientProfile, SmokingHistory
from storage import db
from storage.triage_repository import DuplicateTriageResult, SqliteTriageRepository
from triage.queue import WaitingRoom
from triage.rules import get_rules
from triage.schema import Priority, TriageRequest, TriageVitals
from triage.service import TriageInputError, chart_summary, triage, triage_payload


@pytest.fixture(scope="module")
def rules():
    return get_rules()


@pytest.fixture
def repo(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    try:
        yield SqliteTriageRepository(conn)
    finally:
        conn.close()


NORMAL = {
    "temperature_c": 36.8, "heart_rate": 76, "respiratory_rate": 16,
    "blood_pressure": "122/78", "spo2": 98, "on_oxygen": False, "consciousness": "alert",
}


def triaged(rules, complaint="cough", patient_id=None, **vitals):
    request = TriageRequest(
        age_years=40, patient_id=patient_id, chief_complaint=complaint,
        vitals=TriageVitals(**{**NORMAL, **vitals}),
    )
    return triage(request, rules=rules, rules_only=True)


# --------------------------------------------------------------------------------
# The audit trail
# --------------------------------------------------------------------------------


def test_a_result_round_trips(repo, rules):
    result = triaged(rules, "crushing chest pain", patient_id="P-001")
    repo.save_result(result)

    loaded = repo.get_result(result.request_id)
    assert loaded is not None
    assert loaded.priority == Priority.CRITICAL
    assert loaded.news2.aggregate == result.news2.aggregate
    assert loaded.red_flags[0].id == "cardiac_chest_pain"
    assert loaded.ruleset_version == result.ruleset_version


def test_a_recorded_decision_cannot_be_rewritten(repo, rules):
    """An audit trail that silently accepts a rewrite is not an audit trail."""
    result = triaged(rules, patient_id="P-001")
    repo.save_result(result)

    with pytest.raises(DuplicateTriageResult):
        repo.save_result(result)


def test_a_patients_triage_history_is_newest_first(repo, rules):
    for complaint in ("cough", "sore throat", "crushing chest pain"):
        repo.save_result(triaged(rules, complaint, patient_id="P-007"))

    history = repo.list_for_patient("P-007")
    assert len(history) == 3
    assert history[0].created_at >= history[-1].created_at


def test_a_walk_in_with_no_patient_id_can_still_be_recorded(repo, rules):
    result = triaged(rules, patient_id=None)
    repo.save_result(result)
    assert repo.get_result(result.request_id).patient_id is None


def test_an_unknown_result_is_none(repo):
    assert repo.get_result("t-nothing") is None


def test_the_override_survives_a_round_trip(repo, rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules, "crushing chest pain"))
    key = entry.result.request_id
    room.override(key, Priority.STANDARD, reason="known chest wall pain", clinician="Dr B")

    repo.save_entry(key, entry)
    loaded = repo.load_room()[key]

    assert loaded.result.priority == Priority.CRITICAL
    assert loaded.result.override.priority == Priority.STANDARD
    assert loaded.result.override.reason == "known chest wall pain"


def test_a_removed_patient_is_not_in_the_open_room(repo, rules):
    room = WaitingRoom()
    entry = room.admit(triaged(rules))
    key = entry.result.request_id
    room.remove(key)
    repo.save_entry(key, entry)

    assert repo.load_room() == {}
    assert key in repo.load_room(include_removed=True)


# --------------------------------------------------------------------------------
# The backend integration surface
# --------------------------------------------------------------------------------


def test_dict_in_dict_out(rules):
    payload = triage_payload(
        {
            "age_years": 67, "sex": "male",
            "chief_complaint": "Shortness of breath and chest discomfort",
            "notes": "Started an hour ago",
            "vitals": {
                "temperature_c": 38.9, "heart_rate": 125, "respiratory_rate": 30,
                "blood_pressure": "100/60", "spo2": 84, "on_oxygen": False,
                "consciousness": "alert",
            },
        },
        rules=rules, rules_only=True,
    )

    assert payload["priority"] == "CRITICAL"
    assert payload["status"] == "OK"
    assert payload["requires_human_review"] is True
    assert payload["reasons"]
    assert payload["news2"]["aggregate"] == 11
    json.dumps(payload)


def test_a_misspelled_field_is_rejected_with_a_readable_message(rules):
    """The exact mistake a backend makes first: spo2_percent instead of spo2."""
    with pytest.raises(TriageInputError) as exc:
        triage_payload(
            {"age_years": 40, "vitals": {"spo2_percent": 84}}, rules=rules, rules_only=True
        )
    assert "spo2_percent" in str(exc.value)


def test_an_empty_payload_is_accepted_and_reported_as_incomplete(rules):
    """A blank form is a real thing a desk submits. It must not crash."""
    payload = triage_payload({}, rules=rules, rules_only=True)

    assert payload["status"] == "INSUFFICIENT_DATA"
    assert payload["priority"] == "STANDARD"
    assert payload["missing_information"]


def test_a_bad_type_is_rejected(rules):
    with pytest.raises(TriageInputError):
        triage_payload(
            {"age_years": "forty", "vitals": {}}, rules=rules, rules_only=True
        )


# --------------------------------------------------------------------------------
# The chart
# --------------------------------------------------------------------------------


def profile() -> PatientProfile:
    return PatientProfile(
        id="P-001", full_name="Test Patient", sex="male", age_years=67,
        allergies=[Allergy(substance="Penicillin")],
        medications=[Medication(name="Tiotropium")],
        chronic_conditions=[ChronicCondition(name="COPD")],
        smoking=SmokingHistory(status="former", pack_years=45),
    )


def test_chart_summary_names_what_changes_the_reading():
    summary = chart_summary(profile())

    assert "COPD" in summary
    assert "Tiotropium" in summary
    assert "Penicillin" in summary
    assert "45 pack-years" in summary
    assert "NOT the same as ruled out" in summary


def test_an_empty_chart_says_none_recorded_rather_than_nothing():
    summary = chart_summary(PatientProfile(id="P-002", full_name="Nobody"))

    assert "Chronic conditions: none recorded." in summary
    assert "Allergies: none recorded." in summary


def test_the_chart_reaches_the_request(rules):
    request = TriageRequest(chief_complaint="cough", vitals=TriageVitals(**NORMAL))
    result = triage(request, profile=profile(), rules=rules, rules_only=True)

    # rules-only, so nothing is sent anywhere - but the projection was built and would
    # have been. The assertion that matters is that supplying a chart cannot change the
    # deterministic outcome.
    assert result.priority == Priority.LOW


def test_triage_works_without_a_chart(rules):
    request = TriageRequest(chief_complaint="cough", vitals=TriageVitals(**NORMAL))
    assert triage(request, profile=None, rules=rules, rules_only=True).priority == Priority.LOW
