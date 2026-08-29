"""The triage agent's HTTP surface — the contract Laravel is written against.

These tests exist because the integration boundary is where a rename becomes an outage.
A field the backend reads must keep its name and its meaning, and `priority_rank` in
particular is load-bearing: it is what the waiting room sorts on, and if it ever
disagrees with `priority` the queue silently orders itself wrongly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from service.app import app
from triage.schema import RANK, Priority, TriageRequest, TriageResult, TriageVitals
from triage.service import triage

client = TestClient(app)

NORMAL = {
    "temperature_c": 36.8, "heart_rate": 76, "respiratory_rate": 16,
    "blood_pressure": "122/78", "spo2": 98, "on_oxygen": False, "consciousness": "alert",
}

CRITICAL_VITALS = {
    "temperature_c": 38.9, "heart_rate": 125, "respiratory_rate": 30,
    "blood_pressure": "100/60", "spo2": 84, "on_oxygen": False, "consciousness": "alert",
}


def assess(payload: dict):
    return client.post("/triage/assess", json=payload)


# --------------------------------------------------------------------------------
# priority_rank
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "priority,rank",
    [(Priority.CRITICAL, 3), (Priority.URGENT, 2), (Priority.STANDARD, 1), (Priority.LOW, 0)],
)
def test_rank_orders_most_urgent_highest(priority, rank):
    result = TriageResult(request_id="t-1", priority=priority)
    assert result.priority_rank == rank


def test_rank_is_serialised_with_every_result():
    result = TriageResult(request_id="t-1", priority=Priority.URGENT)
    assert result.model_dump(mode="json")["priority_rank"] == 2


def test_rank_cannot_drift_from_priority():
    for priority in Priority:
        result = TriageResult(request_id="t-1", priority=priority)
        assert result.priority_rank == RANK[result.priority]


def test_a_supplied_rank_is_overwritten_not_trusted():
    """The rank is derived, not accepted.

    A stored row whose rank disagrees with its priority - hand-edited, or written by an
    older version - is corrected when it is read, rather than sorting the queue wrongly
    until someone notices.
    """
    result = TriageResult(request_id="t-1", priority=Priority.CRITICAL, priority_rank=0)
    assert result.priority_rank == 3


def test_a_result_round_trips_through_json_with_its_rank():
    """extra='forbid' plus a serialised derived field is how this broke the first time."""
    original = TriageResult(request_id="t-1", priority=Priority.URGENT)
    restored = TriageResult.model_validate_json(original.model_dump_json())

    assert restored.priority_rank == 2
    assert restored.priority == Priority.URGENT


def test_sorting_by_rank_reproduces_the_intended_order():
    results = [
        TriageResult(request_id="a", priority=Priority.STANDARD),
        TriageResult(request_id="b", priority=Priority.CRITICAL),
        TriageResult(request_id="c", priority=Priority.LOW),
        TriageResult(request_id="d", priority=Priority.URGENT),
    ]
    ordered = sorted(results, key=lambda r: -r.priority_rank)
    assert [r.priority.value for r in ordered] == ["CRITICAL", "URGENT", "STANDARD", "LOW"]


# --------------------------------------------------------------------------------
# POST /triage/assess
# --------------------------------------------------------------------------------


def test_assess_returns_a_decision_for_a_walk_in():
    """No profile, no visit, no patient id. A walk-in must work."""
    response = assess({
        "request": {"age_years": 40, "chief_complaint": "cough", "vitals": NORMAL},
        "rules_only": True,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["priority"] == "LOW"
    assert body["priority_rank"] == 0
    assert body["requires_human_review"] is True
    assert body["ruleset_version"]


def test_assess_scores_a_critical_patient():
    response = assess({
        "request": {
            "age_years": 67,
            "chief_complaint": "shortness of breath and chest discomfort",
            "vitals": CRITICAL_VITALS,
        },
        "rules_only": True,
    })

    body = response.json()
    assert body["priority"] == "CRITICAL"
    assert body["priority_rank"] == 3
    assert body["reasons"]
    assert body["news2"]["aggregate"] > 6


def test_assess_accepts_a_chart_and_uses_it():
    response = assess({
        "request": {"age_years": 70, "chief_complaint": "cough", "vitals": NORMAL},
        "profile": {
            "id": "P-001",
            "full_name": "Test Patient",
            "sex": "male",
            "age_years": 70,
            "chronic_conditions": [{"name": "COPD"}],
        },
        "rules_only": True,
    })

    assert response.status_code == 200
    assert response.json()["patient_id"] is None  # the request said nothing about an id


def test_unknown_field_is_rejected_with_422():
    """extra='forbid' all the way out to the wire, so a Laravel typo fails loudly."""
    response = assess({
        "request": {"age_years": 40, "spo2_percent": 95, "vitals": NORMAL},
        "rules_only": True,
    })
    assert response.status_code == 422


def test_rules_only_result_is_marked_degraded():
    body = assess({
        "request": {"age_years": 40, "chief_complaint": "cough", "vitals": NORMAL},
        "rules_only": True,
    }).json()

    assert body["interpretation"]["available"] is False
    # Still a complete, actionable answer.
    assert body["recommended_action"]
    assert body["status"] == "OK"


def test_out_of_scope_patient_is_reported_as_such():
    body = assess({
        "request": {"age_years": 7, "chief_complaint": "cough", "vitals": NORMAL},
        "rules_only": True,
    }).json()

    assert body["status"] == "OUT_OF_SCOPE"
    assert body["priority"] == "URGENT"


def test_incomplete_observations_never_come_back_low():
    body = assess({
        "request": {
            "age_years": 40, "chief_complaint": "cough",
            "vitals": {"temperature_c": 37.2, "consciousness": "alert"},
        },
        "rules_only": True,
    }).json()

    assert body["status"] == "INSUFFICIENT_DATA"
    assert body["priority_rank"] >= 1
    assert body["missing_information"]


# --------------------------------------------------------------------------------
# GET /reference/triage-rules
# --------------------------------------------------------------------------------


def test_reference_exposes_the_ladder_and_the_flags():
    body = client.get("/reference/triage-rules").json()

    assert [p["priority"] for p in body["priorities"]] == [
        "CRITICAL", "URGENT", "STANDARD", "LOW"
    ]
    assert body["priorities"][0]["rank"] == 3
    assert len(body["red_flags"]) > 10
    assert set(body["actions"]) == {"CRITICAL", "URGENT", "STANDARD", "LOW"}


def test_reference_admits_when_thresholds_are_unverified():
    """The UI can say so. An unverified rule set must not look authoritative."""
    body = client.get("/reference/triage-rules").json()
    assert "verified" in body
    assert isinstance(body["verified"], bool)


def test_health_reports_the_triage_rule_set():
    body = client.get("/health").json()
    assert body["triage"]["status"] == "ok"
    assert body["triage"]["ruleset_version"]


# --------------------------------------------------------------------------------
# The response shape Laravel stores
# --------------------------------------------------------------------------------


def test_every_field_the_backend_persists_is_present():
    """If this fails, a migration column has nothing to hold."""
    result = triage(
        TriageRequest(
            age_years=67,
            chief_complaint="crushing chest pain",
            vitals=TriageVitals(**NORMAL),
        ),
        rules_only=True,
    )
    body = result.model_dump(mode="json")

    for field in (
        "request_id", "priority", "priority_rank", "rule_priority", "status",
        "recommended_action", "reasons", "concerning_findings", "missing_information",
        "data_quality_issues", "requires_human_review", "ruleset_version",
        "engine_version", "ai_escalated", "red_flags", "news2",
    ):
        assert field in body, f"{field} is missing from the serialised result"
