"""Phase 4: the consultation state machine and the record it produces."""

from __future__ import annotations

import json

import pytest
from langchain_core.documents import Document

from clinical.consultation_state import (
    TRANSITIONS,
    InvalidTransition,
    VisitStatus,
    can_transition,
    require_transition,
)
from clinical.session import ConsultationError, ConsultationSession
from patient.profile import Allergy, PatientProfile
from patient.visit import Investigation, PrescribedMedication, Visit, Vitals
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.visit_repository import SqliteVisitRepository

S = VisitStatus

REPLY = json.dumps(
    {
        "differential": [
            {"label": "Community-acquired pneumonia", "likelihood": "most likely",
             "reasoning": "Focal crackles with fever.", "citations": [1]},
            {"label": "COPD exacerbation", "likelihood": "possible", "citations": []},
        ],
        "missing_information": [],
        "concerning_features": ["SpO2 92%"],
        "context_factors": ["45 pack-year history"],
    }
)


class FakeLLM:
    def __init__(self, reply=REPLY):
        self.reply = reply
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return type("Reply", (), {"content": self.reply})()


class FakeRetriever:
    def invoke(self, query):
        return [Document(page_content="Pneumonia presents with crackles.",
                         metadata={"source": "who.pdf", "pdf_page": 56})]


@pytest.fixture
def repos(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    patients = SqlitePatientRepository(conn)
    patients.save(
        PatientProfile(
            id="P-1",
            full_name="Test Patient",
            sex="male",
            age_years=68,
            allergies=[Allergy(substance="Penicillin", severity="severe")],
        )
    )
    yield patients, SqliteVisitRepository(conn)
    conn.close()


@pytest.fixture
def findings():
    return Visit(
        patient_id="P-1",
        chief_complaint="Cough with fever",
        symptoms=["productive cough"],
        vitals=Vitals(temperature_c=38.6, spo2=92),
        physical_exam="Crackles at the left base",
    )


def assessed(repos, findings) -> ConsultationSession:
    """A session that has run its assessment but decided nothing."""
    patients, visits = repos
    session = ConsultationSession.start(patients, visits, "P-1")
    session.apply_findings(findings)
    session.assess(retriever=FakeRetriever(), llm=FakeLLM())
    return session


# --- the transition table --------------------------------------------------------


def test_the_happy_path_is_allowed():
    assert can_transition(S.INITIAL_ASSESSMENT, S.ICD10_SELECTION)
    assert can_transition(S.ICD10_SELECTION, S.TEST_SELECTION)
    assert can_transition(S.TEST_SELECTION, S.WAITING_FOR_TESTS)
    assert can_transition(S.WAITING_FOR_TESTS, S.RESULTS_REVIEW)
    assert can_transition(S.RESULTS_REVIEW, S.TREATMENT_SELECTION)
    assert can_transition(S.TREATMENT_SELECTION, S.COMPLETED)


def test_option_b_skips_the_test_branch():
    assert can_transition(S.ICD10_SELECTION, S.TREATMENT_SELECTION)


def test_results_can_send_the_doctor_back_for_more_tests():
    assert can_transition(S.RESULTS_REVIEW, S.TEST_SELECTION)


def test_the_doctor_can_back_out_of_ordering_tests():
    """Opening the investigation list and deciding against it must have an exit."""
    assert can_transition(S.TEST_SELECTION, S.TREATMENT_SELECTION)


@pytest.mark.parametrize(
    "current,target",
    [
        (S.INITIAL_ASSESSMENT, S.COMPLETED),
        (S.INITIAL_ASSESSMENT, S.TREATMENT_SELECTION),
        (S.ICD10_SELECTION, S.WAITING_FOR_TESTS),
        (S.TEST_SELECTION, S.RESULTS_REVIEW),
        (S.WAITING_FOR_TESTS, S.COMPLETED),
    ],
)
def test_shortcuts_that_would_corrupt_the_record_are_refused(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        require_transition(current, target)


def test_completed_is_terminal():
    assert TRANSITIONS[S.COMPLETED] == frozenset()


def test_the_error_names_the_allowed_moves():
    with pytest.raises(InvalidTransition, match="ICD10_SELECTION"):
        require_transition(S.INITIAL_ASSESSMENT, S.COMPLETED)


# --- nothing is written until a decision is made ---------------------------------


def test_running_an_assessment_saves_nothing(repos, findings):
    patients, visits = repos
    assessed(repos, findings)
    assert visits.list_for_patient("P-1") == []


def test_the_record_appears_when_the_diagnosis_is_chosen(repos, findings):
    _, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia", icd10_code="J18.9")

    stored = visits.list_for_patient("P-1")
    assert len(stored) == 1
    assert stored[0].status is S.ICD10_SELECTION
    assert stored[0].working_diagnosis.icd10_code == "J18.9"


def test_the_assistant_cannot_reach_the_repository(repos, findings):
    """assess() takes no repository, so it has no way to persist anything."""
    import inspect

    from chatbot.consultation import assess

    params = set(inspect.signature(assess).parameters)
    assert params == {"context", "findings", "retriever", "llm"}


# --- suggestion and decision are recorded separately -----------------------------


def test_the_record_keeps_both_what_was_suggested_and_what_was_chosen(repos, findings):
    _, visits = repos
    session = assessed(repos, findings)
    # The assistant ranked pneumonia first; the doctor disagrees.
    session.select_diagnosis("COPD exacerbation", icd10_code="J44.1")

    stored = visits.get(session.visit.id)
    assert stored.working_diagnosis.label == "COPD exacerbation"
    assert [d.label for d in stored.differential] == [
        "Community-acquired pneumonia",
        "COPD exacerbation",
    ]


# --- Option A: investigations ----------------------------------------------------


def test_option_a_leaves_the_visit_open_awaiting_results(repos, findings):
    _, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations(
        [Investigation(name="Chest X-ray", category="radiology"),
         Investigation(name="CBC", category="laboratory")]
    )

    stored = visits.get(session.visit.id)
    assert stored.status is S.WAITING_FOR_TESTS
    assert [i.name for i in stored.ordered_investigations] == ["Chest X-ray", "CBC"]
    assert stored.prescribed_medications == []


# --- Option B: straight to treatment ---------------------------------------------


def test_option_b_reaches_a_completed_record(repos, findings):
    _, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Acute bronchitis", icd10_code="J20.9")
    session.skip_investigations()
    session.prescribe([PrescribedMedication(name="Paracetamol", dose="1 g")])
    session.add_note("Reassured; expect resolution in two weeks.")
    session.complete()

    stored = visits.get(session.visit.id)
    assert stored.status is S.COMPLETED
    assert stored.prescribed_medications[0].name == "Paracetamol"
    assert "Reassured" in stored.doctor_notes


def test_medications_cannot_be_recorded_before_the_treatment_step(repos, findings):
    session = assessed(repos, findings)
    session.select_diagnosis("Acute bronchitis")
    with pytest.raises(ConsultationError):
        session.prescribe([PrescribedMedication(name="Paracetamol")])


# --- resuming an unfinished visit ------------------------------------------------


def test_a_waiting_visit_can_be_resumed_and_finished(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia", icd10_code="J18.9")
    session.choose_investigation_path()
    session.order_investigations([Investigation(name="CBC", category="laboratory")])
    visit_id = session.visit.id

    resumed = ConsultationSession.resume(patients, visits, visit_id)
    assert resumed.visit.status is S.WAITING_FOR_TESTS

    resumed.record_results("WBC 15.8, CRP 120, chest X-ray shows left basal consolidation")
    assert resumed.visit.status is S.RESULTS_REVIEW
    assert resumed.visit.ordered_investigations[0].status == "resulted"

    resumed.proceed_to_treatment()
    resumed.prescribe([PrescribedMedication(name="Doxycycline", dose="100 mg")])
    resumed.complete()

    assert visits.get(visit_id).status is S.COMPLETED


def test_a_resumed_visit_is_not_also_shown_as_its_own_history(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    resumed = ConsultationSession.resume(patients, visits, session.visit.id)

    assert resumed.context.total_visits == 0
    assert "first recorded visit" in resumed.context.to_prompt_text()


def test_results_reach_the_assistant_in_their_own_labelled_block(repos, findings):
    """Results must not be buried in the examination notes — see format_findings."""
    from patient.history import format_findings

    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations([Investigation(name="CBC", category="laboratory")])

    resumed = ConsultationSession.resume(patients, visits, session.visit.id)
    resumed.record_results("WBC 15.8, left basal consolidation")

    assert resumed.visit.results_summary == "WBC 15.8, left basal consolidation"
    assert "Crackles at the left base" not in (resumed.visit.results_summary or "")

    prompt_text = format_findings(resumed.visit)
    assert "INVESTIGATION RESULTS" in prompt_text
    assert "weigh these above the initial presentation" in prompt_text
    assert "WBC 15.8" in prompt_text


# --- the six defects found in Phase 4 testing ------------------------------------


def test_a_second_round_of_tests_does_not_erase_the_first(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations(
        [Investigation(name="Chest X-ray", category="radiology"),
         Investigation(name="CBC", category="laboratory")]
    )
    resumed = ConsultationSession.resume(patients, visits, session.visit.id)
    resumed.record_results("Equivocal film, normal white count")
    resumed.order_more_investigations()
    resumed.order_investigations([Investigation(name="CT Chest", category="radiology")])

    stored = visits.get(session.visit.id)
    assert [i.name for i in stored.ordered_investigations] == ["Chest X-ray", "CBC", "CT Chest"]
    assert stored.ordered_investigations[0].status == "resulted"
    assert stored.ordered_investigations[2].status == "ordered"


def test_ordering_nothing_is_refused(repos, findings):
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    with pytest.raises(ConsultationError, match="at least one"):
        session.order_investigations([])
    assert session.visit.status is S.TEST_SELECTION


def test_backing_out_of_test_selection_goes_to_treatment(repos, findings):
    session = assessed(repos, findings)
    session.select_diagnosis("Viral upper respiratory infection")
    session.choose_investigation_path()
    session.skip_investigations()
    assert session.visit.status is S.TREATMENT_SELECTION


@pytest.mark.parametrize("label", ["", "   ", None])
def test_a_diagnosis_needs_a_label(repos, findings, label):
    session = assessed(repos, findings)
    with pytest.raises(ConsultationError, match="needs a label"):
        session.select_diagnosis(label)
    assert visits_for(session) == 0


def visits_for(session) -> int:
    return len(session._visits.list_for_patient(session.visit.patient_id))


def test_only_the_named_investigations_are_marked_resulted(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations(
        [Investigation(name="CBC", category="laboratory"),
         Investigation(name="CT Chest", category="radiology")]
    )
    resumed = ConsultationSession.resume(patients, visits, session.visit.id)
    resumed.record_results("CBC back, CT still pending", resulted=["CBC"])

    statuses = {i.name: i.status for i in resumed.visit.ordered_investigations}
    assert statuses == {"CBC": "resulted", "CT Chest": "ordered"}


def test_a_revised_diagnosis_also_needs_a_label(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations([Investigation(name="CBC", category="laboratory")])
    resumed = ConsultationSession.resume(patients, visits, session.visit.id)
    resumed.record_results("Normal")
    with pytest.raises(ConsultationError, match="needs a label"):
        resumed.revise_diagnosis("  ")


def test_the_diagnosis_can_only_be_revised_while_reviewing_results(repos, findings):
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    with pytest.raises(ConsultationError):
        session.revise_diagnosis("COPD exacerbation")


def test_revising_the_diagnosis_records_what_it_was(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations([Investigation(name="CBC", category="laboratory")])
    resumed = ConsultationSession.resume(patients, visits, session.visit.id)
    resumed.record_results("Normal white count, no consolidation")
    resumed.revise_diagnosis("Acute bronchitis", icd10_code="J20.9")

    stored = visits.get(resumed.visit.id)
    assert stored.working_diagnosis.label == "Acute bronchitis"
    assert "was: Community-acquired pneumonia" in stored.doctor_notes


def test_a_completed_visit_cannot_be_reopened(repos, findings):
    patients, visits = repos
    session = assessed(repos, findings)
    session.select_diagnosis("Acute bronchitis")
    session.skip_investigations()
    session.complete()

    with pytest.raises(ConsultationError, match="completed"):
        ConsultationSession.resume(patients, visits, session.visit.id)


def test_resuming_an_unknown_visit_is_an_error(repos):
    patients, visits = repos
    with pytest.raises(ConsultationError):
        ConsultationSession.resume(patients, visits, "V-nope")


def test_starting_for_an_unknown_patient_is_an_error(repos):
    patients, visits = repos
    with pytest.raises(ConsultationError):
        ConsultationSession.start(patients, visits, "ghost")
