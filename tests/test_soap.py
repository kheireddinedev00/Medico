"""SOAP notes: the record, rearranged under four headings and nothing more.

The properties worth protecting here are all about what the note must never do —
invent a finding, present the assistant's list as the clinician's, or let an absent
field read as a normal one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from clinical.consultation_state import VisitStatus
from patient.profile import Allergy, ChronicCondition, Medication, PatientProfile, SmokingHistory
from patient.report import StoredReport
from patient.visit import Diagnosis, Investigation, PrescribedMedication, Visit, Vitals
from report_reader.analyzer import FlaggedResult, ReportAnalysis
from soap.note import ASSISTANT_HEADING, build_soap_note, reports_for_visit

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_profile(**kwargs) -> PatientProfile:
    defaults = dict(id="P-1", full_name="Test Patient", sex="male", age_years=68)
    return PatientProfile(**{**defaults, **kwargs})


def make_visit(days=0, **kwargs) -> Visit:
    return Visit(patient_id="P-1", created_at=BASE + timedelta(days=days), **kwargs)


def make_analysis(**kwargs) -> ReportAnalysis:
    defaults = dict(
        summary="Raised white cell count.",
        results=[
            FlaggedResult(parameter="WBC", value="15.8", unit="10^9/L",
                          reference="4 - 11", flag="high", source="computed"),
            FlaggedResult(parameter="Hb", value="13.9", unit="g/dL",
                          flag="normal", source="computed"),
        ],
    )
    return ReportAnalysis(**{**defaults, **kwargs})


# --- what goes where -------------------------------------------------------------


def test_findings_land_in_the_right_sections():
    profile = make_profile(
        allergies=[Allergy(substance="Penicillin", reaction="rash", severity="moderate")],
        chronic_conditions=[ChronicCondition(name="COPD", since="2019")],
        medications=[Medication(name="Salbutamol", dose="100 mcg")],
        smoking=SmokingHistory(status="former", pack_years=40, quit_year=2015),
    )
    visit = make_visit(
        chief_complaint="Productive cough for five days",
        symptoms=["fever", "breathlessness"],
        vitals=Vitals(temperature_c=38.4, spo2=91),
        physical_exam="Crackles at the right base",
        status=VisitStatus.TREATMENT_SELECTION,
        working_diagnosis=Diagnosis(label="Community-acquired pneumonia", icd10_code="J18.9"),
        ordered_investigations=[Investigation(name="Chest X-ray", category="radiology")],
        prescribed_medications=[PrescribedMedication(name="Amoxicillin", dose="500 mg")],
    )

    note = build_soap_note(profile, visit)

    assert note.subjective.chief_complaint == "Productive cough for five days"
    assert note.subjective.symptoms == ["fever", "breathlessness"]
    assert "Penicillin — rash, moderate" in note.subjective.allergies
    assert "COPD (since 2019)" in note.subjective.past_medical_history
    assert "former smoker" in note.subjective.social_history

    assert "Temperature 38.4 °C" in note.objective.vitals
    assert "SpO2 91%" in note.objective.vitals
    assert note.objective.physical_exam == "Crackles at the right base"

    assert note.assessment.working_diagnosis == "Community-acquired pneumonia"
    assert note.assessment.icd10_code == "J18.9"

    assert note.plan.investigations == ["Chest X-ray (radiology, ordered)"]
    assert note.plan.medications == ["Amoxicillin 500 mg"]


def test_stopped_medications_are_not_listed_as_current():
    profile = make_profile(
        medications=[Medication(name="Prednisolone", active=False), Medication(name="Salbutamol")]
    )
    note = build_soap_note(profile, make_visit())
    assert note.subjective.current_medications == ["Salbutamol"]


def test_past_visits_become_background_and_this_visit_is_excluded():
    earlier = make_visit(days=-30, working_diagnosis=Diagnosis(label="COPD exacerbation"))
    today = make_visit(working_diagnosis=Diagnosis(label="Pneumonia"))

    note = build_soap_note(make_profile(), today, history=[today, earlier])

    assert note.subjective.previous_visit_count == 1
    assert any("COPD exacerbation" in line for line in note.subjective.previous_diagnoses)
    assert not any("Pneumonia" in line for line in note.subjective.previous_diagnoses)


def test_a_later_visit_is_not_backdated_into_an_earlier_note():
    """A note describes the encounter as it stood on the day.

    Notes are regenerated from a record that keeps growing, so without this a note for
    a visit in March would quietly acquire background from August and read as history
    the doctor had in front of them at the time.
    """
    march = make_visit(days=0, working_diagnosis=Diagnosis(label="COPD exacerbation"))
    august = make_visit(days=200, working_diagnosis=Diagnosis(label="Pneumonia"))

    note = build_soap_note(make_profile(), march, history=[march, august])

    assert note.subjective.previous_visit_count == 0
    assert note.subjective.previous_diagnoses == ["None on record before this visit."]
    assert "Pneumonia" not in note.to_text()


def test_no_history_supplied_is_not_reported_as_no_previous_visits():
    """Absent input and an empty record are different claims."""
    unknown = build_soap_note(make_profile(), make_visit())
    assert unknown.subjective.previous_diagnoses == []
    assert "Previous diagnoses: Not recorded." in unknown.to_text()

    empty_record = build_soap_note(make_profile(), make_visit(), history=[])
    assert "None on record before this visit." in empty_record.to_text()


def test_history_order_does_not_change_the_note():
    visits = [
        make_visit(days=-30, working_diagnosis=Diagnosis(label="Older")),
        make_visit(days=-5, working_diagnosis=Diagnosis(label="Newer")),
    ]
    today = make_visit()
    forwards = build_soap_note(make_profile(), today, history=visits)
    backwards = build_soap_note(make_profile(), today, history=list(reversed(visits)))
    assert forwards.subjective.previous_diagnoses == backwards.subjective.previous_diagnoses
    assert forwards.subjective.previous_diagnoses[0].endswith("Newer")


# --- absent data -----------------------------------------------------------------


def test_empty_chart_says_not_recorded_rather_than_reading_as_normal():
    text = build_soap_note(make_profile(allergies=[]), make_visit()).to_text()
    assert "Allergies: Not recorded." in text
    # The dangerous failure is silence: no allergy heading at all reads as "checked,
    # nothing found" to a doctor skimming the note.
    assert "no known allergies" not in text.lower()


def test_unfinished_visit_names_the_stage_it_stopped_at():
    visit = make_visit(chief_complaint="Cough", status=VisitStatus.INITIAL_ASSESSMENT)
    text = build_soap_note(make_profile(), visit).to_text()
    assert "still open at INITIAL_ASSESSMENT" in text
    # The headings stay, so a partial note cannot be mistaken for a complete one.
    assert "A — ASSESSMENT" in text and "P — PLAN" in text


def test_every_heading_is_present_in_a_completely_empty_note():
    text = build_soap_note(make_profile(), make_visit()).to_text()
    for heading in ["S — SUBJECTIVE", "O — OBJECTIVE", "A — ASSESSMENT", "P — PLAN"]:
        assert heading in text


# --- the assistant stays labelled ------------------------------------------------


def test_assistant_differential_is_labelled_and_kept_out_of_the_assessment():
    visit = make_visit(
        status=VisitStatus.ICD10_SELECTION,
        working_diagnosis=Diagnosis(label="Community-acquired pneumonia"),
        differential=[
            Diagnosis(label="Community-acquired pneumonia", likelihood="most likely"),
            Diagnosis(label="COPD exacerbation", likelihood="possible"),
        ],
    )
    note = build_soap_note(make_profile(), visit)

    assert note.assessment.working_diagnosis == "Community-acquired pneumonia"
    assert note.assessment.assistant_differential[1].startswith("2. COPD exacerbation")

    text = note.to_text()
    assert ASSISTANT_HEADING in text
    # The doctor's conclusion is printed before the assistant's list, never merged in.
    assert text.index("Working diagnosis") < text.index(ASSISTANT_HEADING)


def test_no_ai_flag_removes_the_differential_entirely():
    visit = make_visit(
        status=VisitStatus.ICD10_SELECTION,
        working_diagnosis=Diagnosis(label="Pneumonia"),
        differential=[Diagnosis(label="COPD exacerbation")],
    )
    note = build_soap_note(make_profile(), visit, include_assistant_differential=False)
    assert note.assessment.assistant_differential == []
    assert "COPD exacerbation" not in note.to_text()
    assert ASSISTANT_HEADING not in note.to_text()


def test_a_likelihood_percentage_never_reaches_the_note():
    """The record refuses to store one; the note has no other source for it."""
    visit = make_visit(differential=[Diagnosis(label="Pneumonia", likelihood="most likely")])
    assert "%" not in build_soap_note(make_profile(), visit).to_text()


# --- reports ----------------------------------------------------------------------


def test_only_this_visits_reports_appear():
    visit = make_visit()
    mine = StoredReport(patient_id="P-1", visit_id=visit.id, report_type="CBC",
                        analysis=make_analysis())
    theirs = StoredReport(patient_id="P-1", visit_id="V-other", report_type="Old CBC")
    assert [r.id for r in reports_for_visit(visit, [mine, theirs])] == [mine.id]


def test_a_report_attached_by_id_is_included_even_without_a_visit_id():
    """Results arrive unlinked and get attached when the visit is reviewed."""
    unlinked = StoredReport(patient_id="P-1", report_type="CBC")
    visit = make_visit(report_ids=[unlinked.id])
    assert reports_for_visit(visit, [unlinked]) == [unlinked]


def test_abnormal_values_are_carried_into_objective():
    report = StoredReport(patient_id="P-1", report_type="CBC", analysis=make_analysis())
    visit = make_visit(report_ids=[report.id])
    note = build_soap_note(make_profile(), visit, reports=[report])

    finding = note.objective.reports[0]
    assert finding.analysed is True
    assert any("WBC" in line for line in finding.abnormal)
    # Normal results are not repeated in the note; the abnormal ones are the finding.
    assert not any("Hb" in line for line in finding.abnormal)


def test_an_unanalysed_report_says_so_rather_than_being_omitted():
    report = StoredReport(patient_id="P-1", report_type="Chest X-ray", kind="radiology")
    visit = make_visit(report_ids=[report.id])
    text = build_soap_note(make_profile(), visit, reports=[report]).to_text()
    assert "Chest X-ray" in text
    assert "not analysed" in text.lower()


# --- output formats ---------------------------------------------------------------


def full_note():
    profile = make_profile(allergies=[Allergy(substance="Penicillin")])
    visit = make_visit(
        chief_complaint="Cough",
        symptoms=["fever"],
        vitals=Vitals(spo2=91),
        status=VisitStatus.TREATMENT_SELECTION,
        working_diagnosis=Diagnosis(label="Pneumonia", icd10_code="J18.9"),
        differential=[Diagnosis(label="COPD exacerbation")],
        ordered_investigations=[Investigation(name="Chest X-ray")],
        prescribed_medications=[PrescribedMedication(name="Amoxicillin")],
        doctor_notes="Review in 48 hours.",
    )
    return build_soap_note(profile, visit)


@pytest.mark.parametrize("render", [lambda n: n.to_text(), lambda n: n.to_markdown()])
def test_both_formats_carry_the_same_clinical_content(render):
    text = render(full_note())
    for value in ["Cough", "fever", "Penicillin", "SpO2 91%", "Pneumonia", "J18.9",
                  "Chest X-ray", "Amoxicillin", "Review in 48 hours."]:
        assert value in text


def test_json_round_trips_through_the_model():
    from soap.note import SoapNote

    note = full_note()
    restored = SoapNote.model_validate(json.loads(note.model_dump_json()))
    assert restored.to_text() == note.to_text()


def test_every_format_carries_the_review_disclaimer():
    note = full_note()
    assert "Not a signed clinical document" in note.to_text()
    assert "Not a signed clinical document" in note.to_markdown()


def test_note_generation_does_not_mutate_the_visit():
    visit = make_visit(chief_complaint="Cough", symptoms=["fever"])
    before = visit.model_dump_json()
    build_soap_note(make_profile(), visit).to_text()
    assert visit.model_dump_json() == before
