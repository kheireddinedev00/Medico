"""Phase 1: the patient record and the context it produces."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from clinical.consultation_state import VisitStatus
from patient.history import build_clinical_context, format_findings
from patient.profile import Allergy, ChronicCondition, Medication, PatientProfile, SmokingHistory
from patient.visit import Diagnosis, Visit, Vitals
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.visit_repository import SqliteVisitRepository

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "test.db"))
    yield connection
    connection.close()


def make_visit(patient_id="P-1", days=0, **kwargs) -> Visit:
    return Visit(patient_id=patient_id, created_at=BASE + timedelta(days=days), **kwargs)


# --- profile ---------------------------------------------------------------------


def test_age_computed_from_date_of_birth():
    born = date.today().replace(year=date.today().year - 40)
    profile = PatientProfile(id="P-1", full_name="T", date_of_birth=born)
    assert profile.age == 40


def test_age_falls_back_to_recorded_age_without_date_of_birth():
    assert PatientProfile(id="P-1", full_name="T", age_years=71).age == 71


def test_age_is_none_when_nothing_recorded():
    assert PatientProfile(id="P-1", full_name="T").age is None


def test_active_medications_excludes_stopped_ones():
    profile = PatientProfile(
        id="P-1",
        full_name="T",
        medications=[Medication(name="Warfarin"), Medication(name="Old drug", active=False)],
    )
    assert [m.name for m in profile.active_medications()] == ["Warfarin"]


def test_unknown_key_is_rejected_not_ignored():
    with pytest.raises(Exception):
        PatientProfile.model_validate({"id": "P-1", "full_name": "T", "allergie": []})


# --- context rendering -----------------------------------------------------------


def test_empty_chart_says_none_recorded_not_no_allergies():
    text = build_clinical_context(PatientProfile(id="P-1", full_name="T"), []).to_prompt_text()
    assert "ALLERGIES: None recorded." in text
    assert "NOT the same as ruled out" in text
    assert "first recorded visit" in text


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, "age and sex not recorded"),
        ({"sex": "female"}, "female, age not recorded"),
        ({"age_years": 71}, "71-year-old, sex not recorded"),
        ({"sex": "male", "age_years": 60}, "60-year-old male"),
    ],
)
def test_demographics_keeps_missing_fields_distinguishable(kwargs, expected):
    profile = PatientProfile(id="P-1", full_name="T", **kwargs)
    assert f"PATIENT: {expected}" in build_clinical_context(profile, []).to_prompt_text()


def test_allergies_and_medications_appear_in_context():
    profile = PatientProfile(
        id="P-1",
        full_name="T",
        allergies=[Allergy(substance="Penicillin", reaction="rash", severity="severe")],
        medications=[Medication(name="Tiotropium", dose="18 mcg", indication="COPD")],
        chronic_conditions=[ChronicCondition(name="COPD", since="2016")],
        smoking=SmokingHistory(status="former", pack_years=45, quit_year=2018),
    )
    text = build_clinical_context(profile, []).to_prompt_text()
    assert "Penicillin — rash, severe" in text
    assert "Tiotropium 18 mcg (for COPD)" in text
    assert "COPD (since 2016)" in text
    assert "former smoker, 45 pack-years, quit 2018" in text


def test_only_recent_visits_render_newest_first():
    profile = PatientProfile(id="P-1", full_name="T")
    visits = [make_visit(days=i, chief_complaint=f"complaint {i}") for i in range(5)]
    context = build_clinical_context(profile, visits, recent_visits=3)

    assert context.total_visits == 5
    assert [v.chief_complaint for v in context.recent_visits] == [
        "complaint 4",
        "complaint 3",
        "complaint 2",
    ]
    text = context.to_prompt_text()
    assert "showing 3 of 5" in text
    assert "complaint 1" not in text


def test_unfinished_encounter_is_flagged():
    profile = PatientProfile(id="P-1", full_name="T")
    visits = [make_visit(status=VisitStatus.WAITING_FOR_TESTS)]
    text = build_clinical_context(profile, visits).to_prompt_text()
    assert "UNFINISHED ENCOUNTERS" in text
    assert "WAITING_FOR_TESTS" in text


def test_completed_visit_is_not_flagged_as_unfinished():
    profile = PatientProfile(id="P-1", full_name="T")
    visits = [make_visit(status=VisitStatus.COMPLETED)]
    assert "UNFINISHED ENCOUNTERS" not in build_clinical_context(profile, visits).to_prompt_text()


def test_previous_diagnoses_are_listed_with_codes():
    profile = PatientProfile(id="P-1", full_name="T")
    visits = [make_visit(working_diagnosis=Diagnosis(label="Pneumonia", icd10_code="J18.9"))]
    assert "Pneumonia [J18.9]" in build_clinical_context(profile, visits).to_prompt_text()


# --- today's findings ------------------------------------------------------------


def test_findings_render_vitals_and_symptoms():
    findings = make_visit(
        chief_complaint="Cough for 4 days",
        symptoms=["productive cough", "fever"],
        vitals=Vitals(temperature_c=38.6, spo2=92),
    )
    text = format_findings(findings)
    assert "Cough for 4 days" in text
    assert "productive cough; fever" in text
    assert "T 38.6C" in text and "SpO2 92%" in text


def test_findings_never_leak_the_doctors_working_diagnosis():
    findings = make_visit(working_diagnosis=Diagnosis(label="Pneumonia"))
    assert "Pneumonia" not in format_findings(findings)


def test_empty_findings_say_so():
    assert "No findings have been recorded" in format_findings(make_visit())


# --- storage ---------------------------------------------------------------------


def test_patient_round_trip(conn):
    repo = SqlitePatientRepository(conn)
    profile = PatientProfile(
        id="P-1",
        full_name="Test Patient",
        sex="male",
        date_of_birth=date(1958, 4, 12),
        allergies=[Allergy(substance="Penicillin", severity="severe")],
    )
    repo.save(profile)

    loaded = repo.get("P-1")
    assert loaded == profile
    assert loaded.allergies[0].substance == "Penicillin"


def test_missing_patient_returns_none(conn):
    assert SqlitePatientRepository(conn).get("nobody") is None


def test_saving_twice_updates_rather_than_duplicates(conn):
    repo = SqlitePatientRepository(conn)
    repo.save(PatientProfile(id="P-1", full_name="Before"))
    repo.save(PatientProfile(id="P-1", full_name="After"))

    assert len(repo.list_all()) == 1
    assert repo.get("P-1").full_name == "After"


def test_visit_round_trip_preserves_status_and_vitals(conn):
    SqlitePatientRepository(conn).save(PatientProfile(id="P-1", full_name="T"))
    repo = SqliteVisitRepository(conn)
    visit = make_visit(
        status=VisitStatus.WAITING_FOR_TESTS,
        symptoms=["cough"],
        vitals=Vitals(spo2=92),
        working_diagnosis=Diagnosis(label="Pneumonia", icd10_code="J18.9"),
    )
    repo.save(visit)

    loaded = repo.get(visit.id)
    assert loaded.status is VisitStatus.WAITING_FOR_TESTS
    assert loaded.symptoms == ["cough"]
    assert loaded.vitals.spo2 == 92
    assert loaded.working_diagnosis.icd10_code == "J18.9"


def test_visits_come_back_newest_first_and_respect_limit(conn):
    SqlitePatientRepository(conn).save(PatientProfile(id="P-1", full_name="T"))
    repo = SqliteVisitRepository(conn)
    for i in range(3):
        repo.save(make_visit(days=i, chief_complaint=f"c{i}"))

    assert [v.chief_complaint for v in repo.list_for_patient("P-1")] == ["c2", "c1", "c0"]
    assert [v.chief_complaint for v in repo.list_for_patient("P-1", limit=2)] == ["c2", "c1"]


def test_visit_for_unknown_patient_is_rejected(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        SqliteVisitRepository(conn).save(make_visit(patient_id="ghost"))
