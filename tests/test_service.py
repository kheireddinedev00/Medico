"""The HTTP wrapper, exercised end to end.

Nothing here calls the model. The consultation routes are the state machine over HTTP and
the SOAP route is a projection, so the whole workflow can be walked without spending a
token or touching the network — which is also why this suite is safe to run in a loop.

What these tests are actually protecting:

- the `persist` flag, which is how "nothing is written until a diagnosis is chosen" reaches
  Laravel without being reimplemented in PHP;
- the transition rules, which must be refused by the service even when a caller asks
  politely;
- the round trip, because a `Visit` that cannot survive being serialised, sent and parsed
  back would break every route and only show up under a real client.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from config import ICD10_CODE_LIST
from service.app import app

client = TestClient(app)


PROFILE = {
    "id": "P-TEST",
    "full_name": "Test Patient",
    "sex": "male",
    "date_of_birth": "1960-01-01",
    "allergies": [
        {"substance": "Penicillin", "reaction": "urticaria", "severity": "severe"}
    ],
    "medications": [
        {"name": "Tiotropium", "dose": "18 mcg", "frequency": "daily", "active": True}
    ],
    "chronic_conditions": [{"name": "COPD", "since": "2016"}],
    "smoking": {"status": "former", "pack_years": 40, "quit_year": 2018},
}


def chart(visit: dict, history: list | None = None, reports: list | None = None) -> dict:
    return {
        "profile": PROFILE,
        "visit": visit,
        "history": history or [],
        "reports": reports or [],
    }


def post(path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def new_visit() -> dict:
    result = post("/consultation/start", {"profile": PROFILE})
    return result["visit"]


# --------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------


def curated_codes() -> list[dict]:
    """The curated list as it sits on disk, so the tests follow it rather than pin it."""
    return json.loads(ICD10_CODE_LIST.read_text(encoding="utf-8"))["codes"]


def test_health_reports_which_icd10_mode_is_active():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # The list is populated in this repo, so suggestions are validated, not shape-checked.
    assert body["icd10_mode"] == "validated"
    # The count is read from the list rather than written down here: pinning the number
    # meant that curating one new code failed two tests that had nothing to say about it.
    assert body["icd10_codes"] == len(curated_codes())


def test_workflow_table_is_served_rather_than_duplicated_in_the_ui():
    body = client.get("/reference/workflow").json()
    assert body["transitions"]["INITIAL_ASSESSMENT"] == ["ICD10_SELECTION"]
    assert body["transitions"]["COMPLETED"] == []
    assert sorted(body["transitions"]["ICD10_SELECTION"]) == [
        "TEST_SELECTION",
        "TREATMENT_SELECTION",
    ]


def test_icd10_reference_is_servable_for_seeding():
    body = client.get("/reference/icd10").json()
    assert body["mode"] == "validated"
    assert len(body["codes"]) == len(curated_codes())
    assert any(entry["code"] == "J18.9" for entry in body["codes"])


# --------------------------------------------------------------------------------
# The persist flag
# --------------------------------------------------------------------------------


def test_a_new_visit_is_not_part_of_the_record(new_visit):
    """A doctor who opens a patient and walks away must leave nothing behind."""
    result = post("/consultation/findings", {
        "chart": chart(new_visit),
        "findings": {
            "patient_id": "P-TEST",
            "chief_complaint": "Breathlessness for three days",
            "symptoms": ["cough", "fever"],
        },
    })
    assert result["persist"] is False
    assert result["visit"]["chief_complaint"] == "Breathlessness for three days"


def test_the_record_begins_when_the_physician_chooses_a_diagnosis(new_visit):
    result = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit),
        "label": "Infective exacerbation of COPD",
    })
    assert result["persist"] is True
    assert result["visit"]["status"] == "ICD10_SELECTION"
    assert result["visit"]["working_diagnosis"]["label"] == "Infective exacerbation of COPD"


# --------------------------------------------------------------------------------
# The whole of option A, over HTTP
# --------------------------------------------------------------------------------


def test_full_consultation_through_investigations(new_visit):
    visit = post("/consultation/findings", {
        "chart": chart(new_visit),
        "findings": {
            "patient_id": "P-TEST",
            "chief_complaint": "Productive cough and fever",
            "symptoms": ["cough", "fever", "pleuritic pain"],
            "vitals": {"temperature_c": 38.4, "spo2": 92.0, "blood_pressure": "128/76"},
        },
    })["visit"]

    visit = post("/consultation/select-diagnosis", {
        "chart": chart(visit), "label": "Community-acquired pneumonia",
    })["visit"]

    visit = post("/consultation/icd10-code", {"chart": chart(visit), "code": "j18.9"})["visit"]
    # Normalised on the way in, so the record never holds two spellings of one code.
    assert visit["working_diagnosis"]["icd10_code"] == "J18.9"

    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    assert visit["status"] == "TEST_SELECTION"

    visit = post("/consultation/order-investigations", {
        "chart": chart(visit),
        "investigations": [
            {"name": "Chest X-ray", "category": "radiology", "rationale": "Confirm consolidation"},
            {"name": "CRP", "category": "laboratory", "rationale": "Inflammatory marker"},
        ],
    })["visit"]
    assert visit["status"] == "WAITING_FOR_TESTS"
    assert len(visit["ordered_investigations"]) == 2

    visit = post("/consultation/record-results", {
        "chart": chart(visit),
        "summary": "Chest X-ray: right lower lobe consolidation. CRP 142 mg/L.",
        "resulted": ["Chest X-ray", "CRP"],
    })["visit"]
    assert visit["status"] == "RESULTS_REVIEW"
    assert all(i["status"] == "resulted" for i in visit["ordered_investigations"])

    visit = post("/consultation/treat", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/prescribe", {
        "chart": chart(visit),
        "medications": [
            {"name": "Doxycycline", "dose": "100 mg", "frequency": "twice daily",
             "duration": "5 days"}
        ],
    })["visit"]
    visit = post("/consultation/complete", {"chart": chart(visit)})["visit"]
    assert visit["status"] == "COMPLETED"


def test_a_second_round_of_investigations_does_not_erase_the_first(new_visit):
    """The results already recorded refer to the first round. Losing it corrupts the note."""
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Community-acquired pneumonia",
    })["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit), "investigations": [{"name": "CRP", "category": "laboratory"}],
    })["visit"]
    visit = post("/consultation/record-results", {
        "chart": chart(visit), "summary": "CRP 142 mg/L.",
    })["visit"]
    visit = post("/consultation/order-more-investigations", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit), "investigations": [{"name": "Blood culture", "category": "laboratory"}],
    })["visit"]

    names = [i["name"] for i in visit["ordered_investigations"]]
    assert names == ["CRP", "Blood culture"]


# --------------------------------------------------------------------------------
# What the service refuses
# --------------------------------------------------------------------------------


def test_a_visit_cannot_be_completed_without_a_diagnosis(new_visit):
    response = client.post("/consultation/complete", json={"chart": chart(new_visit)})
    assert response.status_code == 409
    assert response.json()["error"] == "invalid_transition"


def test_results_cannot_be_recorded_before_tests_are_ordered(new_visit):
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Asthma exacerbation",
    })["visit"]
    response = client.post(
        "/consultation/record-results", json={"chart": chart(visit), "summary": "Normal."}
    )
    assert response.status_code == 409


def test_a_diagnosis_cannot_be_revised_outside_results_review(new_visit):
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Asthma exacerbation",
    })["visit"]
    response = client.post(
        "/consultation/revise-diagnosis", json={"chart": chart(visit), "label": "COPD"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "consultation_error"


def test_a_completed_visit_cannot_be_reopened(new_visit):
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Acute bronchitis",
    })["visit"]
    visit = post("/consultation/skip-investigations", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/complete", {"chart": chart(visit)})["visit"]

    response = client.post("/consultation/note", json={"chart": chart(visit), "note": "late"})
    assert response.status_code == 409
    assert "completed" in response.json()["detail"].lower()


def test_an_unknown_field_is_rejected_rather_than_dropped(new_visit):
    """extra='forbid' reaching all the way out to the HTTP boundary."""
    bad = {**new_visit, "triage_score": 7}
    response = client.post(
        "/consultation/note", json={"chart": chart(bad), "note": "hello"}
    )
    assert response.status_code == 422


def test_ordering_no_investigations_is_refused_by_validation(new_visit):
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Community-acquired pneumonia",
    })["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    response = client.post(
        "/consultation/order-investigations",
        json={"chart": chart(visit), "investigations": []},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------------
# Introspection and SOAP
# --------------------------------------------------------------------------------


def test_next_states_lets_the_ui_disable_what_the_engine_would_refuse(new_visit):
    body = post("/consultation/next-states", {"chart": chart(new_visit)})
    assert body == {"status": "INITIAL_ASSESSMENT", "next": ["ICD10_SELECTION"]}


def test_soap_note_is_built_from_the_record_alone(new_visit):
    visit = post("/consultation/findings", {
        "chart": chart(new_visit),
        "findings": {
            "patient_id": "P-TEST",
            "chief_complaint": "Productive cough",
            "symptoms": ["cough"],
            "vitals": {"spo2": 92.0},
        },
    })["visit"]
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(visit), "label": "Community-acquired pneumonia",
    })["visit"]

    note = post("/soap", {"chart": chart(visit)})
    assert "Productive cough" in note["subjective"]["chief_complaint"]
    assert note["assessment"]["working_diagnosis"].startswith("Community-acquired pneumonia")
    # The allergy has to survive into the note — it is the thing a prescriber must see.
    assert any("Penicillin" in line for line in note["subjective"]["allergies"])


# --------------------------------------------------------------------------------
# Screening what the physician chose themselves
# --------------------------------------------------------------------------------


def test_a_drug_the_physician_typed_is_screened_too(new_visit):
    """The assistant's suggestions are screened before anyone sees them; a drug typed by
    hand bypasses that entirely. The physician is the decision-maker, but a prescriber
    working from memory can still miss an allergy someone else recorded months ago."""
    body = post("/assistant/check-medications", {
        "chart": chart(new_visit),
        "names": ["Amoxicillin", "Doxycycline"],
    })

    blocked = {w["medication"] for w in body["blocked"]}
    assert "Amoxicillin" in blocked, "P-TEST is recorded as allergic to penicillin."
    assert "Doxycycline" not in blocked


def test_a_brand_name_is_screened_like_its_generic(new_visit):
    """A model — or a doctor — will sometimes write "Augmentin". A screen that only knew
    "co-amoxiclav" would hand it to a penicillin-allergic patient without a word."""
    body = post("/assistant/check-medications", {
        "chart": chart(new_visit), "names": ["Augmentin"],
    })

    assert [w["medication"] for w in body["blocked"]] == ["Augmentin"]


def test_the_screen_reports_rather_than_removes(new_visit):
    """Nothing is filtered out here. The physician is told what the screen found and
    decides; a system that refused their prescription would be a different product."""
    body = post("/assistant/check-medications", {
        "chart": chart(new_visit), "names": ["Amoxicillin"],
    })

    assert set(body) == {"blocked", "cautions"}
    assert body["blocked"][0]["reason"]


def test_results_can_be_recorded_more_than_once(new_visit):
    """Results arrive in batches — the film today, the culture on Thursday.

    Recording a second set while already reviewing appends rather than being refused. The
    transition table is unchanged: RESULTS_REVIEW still has exactly its two exits, and
    arriving where you already are is simply not a move.
    """
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Community-acquired pneumonia",
    })["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit),
        "investigations": [{"name": "Chest X-ray"}, {"name": "Blood culture"}],
    })["visit"]

    visit = post("/consultation/record-results", {
        "chart": chart(visit), "summary": "Chest X-ray: right lower lobe consolidation.",
        "resulted": ["Chest X-ray"],
    })["visit"]
    assert visit["status"] == "RESULTS_REVIEW"

    # The culture comes back two days later.
    visit = post("/consultation/record-results", {
        "chart": chart(visit), "summary": "Blood culture: no growth at 48 hours.",
        "resulted": ["Blood culture"],
    })["visit"]

    assert visit["status"] == "RESULTS_REVIEW"
    # Both are kept. Losing the first would leave the note describing findings for a test
    # it no longer mentions.
    assert "consolidation" in visit["results_summary"]
    assert "no growth" in visit["results_summary"]
    assert all(i["status"] == "resulted" for i in visit["ordered_investigations"])


def test_results_review_still_has_only_its_two_exits(new_visit):
    """Guards the claim above: appending must not have added an edge to the table."""
    body = client.get("/reference/workflow").json()

    assert sorted(body["transitions"]["RESULTS_REVIEW"]) == [
        "TEST_SELECTION",
        "TREATMENT_SELECTION",
    ]


def test_a_revised_diagnosis_can_be_recoded(new_visit):
    """Revising is allowed at RESULTS_REVIEW; coding has to be, too.

    Otherwise a physician changes the diagnosis after seeing results and is left unable to
    change its code — so the previous diagnosis's code stays attached to the new one, which
    is worse in a record than having no code at all.
    """
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Community-acquired pneumonia",
    })["visit"]
    visit = post("/consultation/icd10-code", {"chart": chart(visit), "code": "J18.9"})["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit), "investigations": [{"name": "CT pulmonary angiogram"}],
    })["visit"]
    visit = post("/consultation/record-results", {
        "chart": chart(visit), "summary": "CTPA: segmental pulmonary embolus.",
    })["visit"]

    visit = post("/consultation/revise-diagnosis", {
        "chart": chart(visit), "label": "Pulmonary embolism",
    })["visit"]
    # The old code does not survive the revision — it described a different illness.
    assert visit["working_diagnosis"]["icd10_code"] is None

    visit = post("/consultation/icd10-code", {"chart": chart(visit), "code": "I26.9"})["visit"]
    assert visit["working_diagnosis"]["icd10_code"] == "I26.9"
    assert visit["status"] == "RESULTS_REVIEW"


def test_coding_is_still_refused_where_there_is_nothing_to_code(new_visit):
    """Relaxing where coding is allowed must not make it allowed everywhere."""
    response = client.post(
        "/consultation/icd10-code", json={"chart": chart(new_visit), "code": "J18.9"}
    )

    assert response.status_code == 409
    assert "working diagnosis" in response.json()["detail"].lower()


def test_results_can_be_corrected_without_moving_the_visit(new_visit):
    """Recording appends, which is right while results arrive separately — but appending
    alone leaves no way to fix a typo or tidy three runs of machine output into something
    readable. Amending replaces, and is deliberately not a transition."""
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Suspected pulmonary embolism",
    })["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit), "investigations": [{"name": "D-dimer"}],
    })["visit"]
    visit = post("/consultation/record-results", {
        "chart": chart(visit), "summary": "D-dimer 140 typo hree",
    })["visit"]

    visit = post("/consultation/amend-results", {
        "chart": chart(visit), "summary": "D-dimer 140 mg/mL DDU (ref < 243) — normal.",
    })["visit"]

    assert visit["results_summary"] == "D-dimer 140 mg/mL DDU (ref < 243) — normal."
    # The visit has not moved: correcting what results say is not deciding what they mean.
    assert visit["status"] == "RESULTS_REVIEW"


def test_results_cannot_be_amended_into_nothing(new_visit):
    """Emptying the field would leave the record claiming tests were reviewed with nothing
    to show for it."""
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Pneumonia",
    })["visit"]
    visit = post("/consultation/investigate", {"chart": chart(visit)})["visit"]
    visit = post("/consultation/order-investigations", {
        "chart": chart(visit), "investigations": [{"name": "CRP"}],
    })["visit"]
    visit = post("/consultation/record-results", {"chart": chart(visit), "summary": "CRP 142."})["visit"]

    response = client.post(
        "/consultation/amend-results", json={"chart": chart(visit), "summary": "   "}
    )

    assert response.status_code == 409


def test_results_cannot_be_amended_before_there_are_any(new_visit):
    visit = post("/consultation/select-diagnosis", {
        "chart": chart(new_visit), "label": "Pneumonia",
    })["visit"]

    response = client.post(
        "/consultation/amend-results", json={"chart": chart(visit), "summary": "anything"}
    )

    assert response.status_code == 409
    assert "RESULTS_REVIEW" in response.json()["detail"]
