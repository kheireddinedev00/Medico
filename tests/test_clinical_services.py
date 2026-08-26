"""Phase 5: ICD-10, investigations, and the medication safety screen.

The screen tests matter most. They assert that a drug the patient is allergic to cannot
reach the physician's screen even when the model suggests it — which is the one thing in
this project that must hold whatever the model does.
"""

from __future__ import annotations

import json

import pytest

from clinical import icd10
from clinical.investigations import (
    InvestigationAdvice,
    SuggestedInvestigation,
    normalise_category,
)
from clinical.medications import (
    MedicationAdvice,
    RawMedicationAdvice,
    SuggestedMedication,
    allergy_classes,
    drug_classes,
    screen,
)
from clinical.session import ConsultationError, ConsultationSession
from patient.profile import Allergy, ChronicCondition, Medication, PatientProfile
from patient.visit import Visit
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.visit_repository import SqliteVisitRepository


def profile(**kwargs) -> PatientProfile:
    return PatientProfile(id="P-1", full_name="Test Patient", **kwargs)


def suggest(*names: str) -> RawMedicationAdvice:
    return RawMedicationAdvice(
        medications=[SuggestedMedication(name=n, rationale="because") for n in names]
    )


# --- drug classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Amoxicillin", "penicillin"),
        ("Co-amoxiclav 625 mg", "penicillin"),
        ("Flucloxacillin", "penicillin"),
        ("Clarithromycin", "macrolide"),
        ("Doxycycline", "tetracycline"),
        ("Co-trimoxazole", "sulfonamide"),
        ("Ciprofloxacin", "quinolone"),
        ("Ibuprofen", "nsaid"),
        ("Cefuroxime", "cephalosporin"),
    ],
)
def test_drugs_are_placed_in_their_class(name, expected):
    assert expected in drug_classes(name)


def test_an_unknown_drug_has_no_class():
    assert drug_classes("Tiotropium") == set()


@pytest.mark.parametrize("brand", ["Augmentin", "Amoxil", "Augmentin 625mg"])
def test_brand_names_are_caught_too(brand):
    """A model that writes 'Augmentin' must not slip past a penicillin allergy."""
    allergic = profile(allergies=[Allergy(substance="Penicillin")])
    result = screen(suggest(brand), allergic)
    assert result.medications == []
    assert result.withheld[0].medication == brand


@pytest.mark.parametrize(
    "recorded,expected",
    [
        ("Penicillin", "penicillin"),
        ("Penicillin V", "penicillin"),
        ("Sulfonamides", "sulfonamide"),
        ("Sulfa drugs", "sulfonamide"),
        ("Aspirin", "aspirin"),
        ("Co-amoxiclav", "penicillin"),
    ],
)
def test_free_text_allergies_map_to_a_class(recorded, expected):
    found = allergy_classes(profile(allergies=[Allergy(substance=recorded)]))
    assert expected in found


# --- the safety screen: withholding ----------------------------------------------


def test_a_penicillin_allergic_patient_is_never_offered_penicillin():
    allergic = profile(allergies=[Allergy(substance="Penicillin", severity="severe")])
    result = screen(suggest("Amoxicillin", "Doxycycline"), allergic)

    assert [m.name for m in result.medications] == ["Doxycycline"]
    assert [w.medication for w in result.withheld] == ["Amoxicillin"]
    assert "recorded allergy to Penicillin" in result.withheld[0].reason


def test_every_member_of_the_allergic_class_is_withheld():
    allergic = profile(allergies=[Allergy(substance="Penicillin")])
    result = screen(suggest("Co-amoxiclav", "Ampicillin", "Flucloxacillin"), allergic)

    assert result.medications == []
    assert len(result.withheld) == 3


def test_a_sulfa_allergy_withholds_co_trimoxazole():
    allergic = profile(allergies=[Allergy(substance="Sulfonamides")])
    result = screen(suggest("Co-trimoxazole", "Amoxicillin"), allergic)

    assert [m.name for m in result.medications] == ["Amoxicillin"]
    assert result.withheld[0].medication == "Co-trimoxazole"


def test_nothing_is_withheld_when_there_are_no_allergies():
    result = screen(suggest("Amoxicillin", "Doxycycline"), profile())
    assert len(result.medications) == 2
    assert result.withheld == []


# --- the safety screen: cautions -------------------------------------------------


def test_warfarin_flags_the_antibiotics_that_raise_inr():
    on_warfarin = profile(
        medications=[Medication(name="Warfarin", dose="5 mg", indication="AF")]
    )
    result = screen(suggest("Clarithromycin"), on_warfarin)

    assert [m.name for m in result.medications] == ["Clarithromycin"]
    assert any("raises INR" in w.reason for w in result.cautions)
    assert result.cautions[0].severity == "caution"


def test_warfarin_does_not_flag_an_unrelated_drug():
    on_warfarin = profile(medications=[Medication(name="Warfarin")])
    assert screen(suggest("Salbutamol"), on_warfarin).cautions == []


def test_a_stopped_medication_does_not_raise_an_interaction():
    stopped = profile(medications=[Medication(name="Warfarin", active=False)])
    assert screen(suggest("Clarithromycin"), stopped).cautions == []


def test_penicillin_allergy_cautions_against_cephalosporins_without_withholding():
    allergic = profile(allergies=[Allergy(substance="Penicillin")])
    result = screen(suggest("Cefuroxime"), allergic)

    assert [m.name for m in result.medications] == ["Cefuroxime"]
    assert any("cross-reactivity" in w.reason for w in result.cautions)


def test_aspirin_allergy_cautions_against_nsaids():
    allergic = profile(allergies=[Allergy(substance="Aspirin", reaction="wheeze")])
    result = screen(suggest("Ibuprofen"), allergic)

    assert any("aspirin-exacerbated" in w.reason for w in result.cautions)


def test_steroids_are_flagged_in_a_diabetic_patient():
    diabetic = profile(chronic_conditions=[ChronicCondition(name="Type 2 diabetes")])
    result = screen(suggest("Prednisolone"), diabetic)

    assert [m.name for m in result.medications] == ["Prednisolone"]
    assert any("blood glucose" in w.reason for w in result.cautions)


def test_nsaids_are_flagged_in_asthma():
    asthmatic = profile(chronic_conditions=[ChronicCondition(name="Asthma")])
    assert any("bronchospasm" in w.reason for w in screen(suggest("Ibuprofen"), asthmatic).cautions)


def test_cautions_are_attached_to_the_right_medication():
    patient = profile(medications=[Medication(name="Warfarin")])
    result = screen(suggest("Clarithromycin", "Salbutamol"), patient)

    clarithromycin, salbutamol = result.medications
    assert result.cautions_for(clarithromycin)
    assert result.cautions_for(salbutamol) == []


def test_non_drug_advice_passes_through_untouched():
    raw = RawMedicationAdvice(
        medications=[], non_drug_advice=["Stop smoking"], notes=["Review in a week"]
    )
    result = screen(raw, profile())
    assert result.non_drug_advice == ["Stop smoking"]
    assert result.notes == ["Review in a week"]


# --- the fixtures the project ships with -----------------------------------------


def test_the_seeded_patients_behave_as_intended():
    """P-001 and P-003 exist to exercise exactly these two rules."""
    from storage.seed import load_seed

    patients, _ = load_seed()
    by_id = {p.id: p for p in patients}

    kamel = screen(suggest("Amoxicillin", "Doxycycline"), by_id["P-001"])
    assert [m.name for m in kamel.medications] == ["Doxycycline"]

    rachid = screen(suggest("Clarithromycin", "Co-trimoxazole"), by_id["P-003"])
    assert [w.medication for w in rachid.withheld] == ["Co-trimoxazole"]
    assert any("warfarin" in w.reason for w in rachid.cautions)


# --- ICD-10 ----------------------------------------------------------------------


@pytest.mark.parametrize("code", ["J18.9", "J44.1", "J45.901", "A15", "U07.1"])
def test_well_formed_codes_are_kept(code):
    assert icd10.SuggestedCode(code=code).is_well_formed()


@pytest.mark.parametrize("code", ["J189", "18.9", "JJ8.9", "", "pneumonia", "J18.99999"])
def test_malformed_codes_are_rejected(code):
    assert not icd10.SuggestedCode(code=code).is_well_formed()


def test_only_well_formed_codes_are_offered():
    advice = icd10.CodeAdvice(
        codes=[
            icd10.SuggestedCode(code="J18.9", description="Pneumonia, unspecified"),
            icd10.SuggestedCode(code="not a code"),
        ]
    )
    assert [c.code for c in advice.well_formed()] == ["J18.9"]


# --- investigations --------------------------------------------------------------


@pytest.mark.parametrize(
    "name,stated,expected",
    [
        ("Chest X-ray", None, "radiology"),
        ("CT Chest", None, "radiology"),
        ("HRCT", "imaging", "radiology"),
        ("CBC", None, "laboratory"),
        ("D-dimer", None, "laboratory"),
        ("Sputum culture", None, "laboratory"),
        ("Peak flow", None, "other"),
        ("Spirometry", "other", "other"),
        ("CRP", "lab", "laboratory"),
    ],
)
def test_categories_are_normalised(name, stated, expected):
    assert normalise_category(name, stated) == expected


def test_a_suggestion_converts_to_a_record_investigation():
    suggestion = SuggestedInvestigation(
        name="Chest X-ray", category="imaging", rationale="Confirm consolidation"
    )
    investigation = suggestion.to_investigation()
    assert investigation.category == "radiology"
    assert investigation.status == "ordered"
    assert investigation.rationale == "Confirm consolidation"


# --- the session refuses to advise without a diagnosis ---------------------------


@pytest.fixture
def session(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    patients = SqlitePatientRepository(conn)
    patients.save(profile(allergies=[Allergy(substance="Penicillin")]))
    session = ConsultationSession.start(patients, SqliteVisitRepository(conn), "P-1")
    session.apply_findings(Visit(patient_id="P-1", chief_complaint="cough"))
    yield session
    conn.close()


@pytest.mark.parametrize(
    "method", ["suggest_codes", "suggest_investigations", "suggest_medications"]
)
def test_no_advice_before_a_diagnosis_is_chosen(session, method):
    with pytest.raises(ConsultationError, match="working diagnosis"):
        getattr(session, method)()


def test_the_session_screens_medications_before_returning_them(session):
    """No interface can obtain unscreened suggestions — the screen is inside the session."""
    class FakeLLM:
        def invoke(self, messages):
            reply = json.dumps(
                {
                    "medications": [
                        {"name": "Amoxicillin", "dose": "500 mg", "rationale": "cover"},
                        {"name": "Doxycycline", "dose": "100 mg", "rationale": "cover"},
                    ],
                    "non_drug_advice": [],
                    "notes": [],
                }
            )
            return type("Reply", (), {"content": reply})()

    session.select_diagnosis("Community-acquired pneumonia")
    advice = session.suggest_medications(llm=FakeLLM())

    assert isinstance(advice, MedicationAdvice)
    assert [m.name for m in advice.medications] == ["Doxycycline"]
    assert advice.withheld[0].medication == "Amoxicillin"


def test_a_code_can_only_be_attached_at_the_coding_step(session):
    session.select_diagnosis("Community-acquired pneumonia")
    session.set_icd10_code("j18.9")
    assert session.visit.working_diagnosis.icd10_code == "J18.9"

    session.skip_investigations()
    with pytest.raises(ConsultationError, match="ICD10_SELECTION"):
        session.set_icd10_code("J20.9")
