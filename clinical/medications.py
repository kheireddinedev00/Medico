"""Medication suggestions, and the safety screen they must survive.

The screen is the point of this module. The model is asked to respect allergies and
interactions, and it usually does — but "usually" is not a safety property. Everything
the model returns is checked in code against the patient's recorded allergies, current
medications and chronic conditions before a physician ever sees it.

Two tiers:

**Withheld.** The drug belongs to a class the patient is recorded as allergic to. It is
removed from the suggestions entirely, and shown separately with the reason. The
requirement was that a penicillin-allergic patient is never recommended penicillin; a
suggestion the doctor has to notice and reject is still a recommendation.

**Caution.** An interaction with a current medication, a cross-reactivity risk, or a
chronic condition that makes the drug harder to use. These stay in the list with the
warning attached, because they are prescribing decisions, not prohibitions.

The tables below are deliberately small and readable. They cover the drugs that come up
in respiratory primary care, and they are the kind of thing a real deployment would
replace with a licensed interaction database — which is a procurement decision, not a
reason to ship nothing.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from patient.profile import PatientProfile
from patient.visit import PrescribedMedication

WarningSeverity = Literal["withheld", "caution"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------
# Clinical knowledge tables
# --------------------------------------------------------------------------------

# Allergy substance (as recorded on the profile) -> drugs in that class.
ALLERGY_CLASSES: dict[str, list[str]] = {
    # Brand names are listed alongside generics on purpose. A model asked for treatment
    # will sometimes write "Augmentin", and a screen that only knows "co-amoxiclav"
    # would hand it to a penicillin-allergic patient without a word.
    "penicillin": [
        "penicillin", "amoxicillin", "amoxicillin-clavulanate", "co-amoxiclav",
        "ampicillin", "flucloxacillin", "benzylpenicillin", "phenoxymethylpenicillin",
        "piperacillin", "tazocin", "augmentin", "amoxil", "clamoxyl",
    ],
    "sulfonamide": [
        "sulfamethoxazole", "co-trimoxazole", "trimethoprim-sulfamethoxazole",
        "sulfadiazine", "sulfasalazine", "septrin", "bactrim",
    ],
    "aspirin": ["aspirin", "acetylsalicylic acid"],
    "nsaid": ["ibuprofen", "naproxen", "diclofenac", "ketorolac", "indometacin"],
    "macrolide": ["erythromycin", "clarithromycin", "azithromycin"],
    "quinolone": ["ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin"],
    "tetracycline": ["doxycycline", "tetracycline", "minocycline", "lymecycline"],
    "cephalosporin": ["cefalexin", "cefuroxime", "ceftriaxone", "cefixime", "cefaclor"],
}

# How a recorded allergy is matched to a class key. Written as substrings because
# allergies are recorded as free text ("Sulfonamides", "Sulfa drugs", "Penicillin V").
ALLERGY_ALIASES: dict[str, list[str]] = {
    "penicillin": ["penicillin", "amoxicillin", "co-amoxiclav", "augmentin"],
    "sulfonamide": ["sulfonamide", "sulfa", "co-trimoxazole", "septrin", "bactrim"],
    "aspirin": ["aspirin", "acetylsalicylic", "salicylate"],
    "nsaid": ["nsaid", "ibuprofen", "naproxen", "diclofenac", "anti-inflammatory"],
    "macrolide": ["macrolide", "erythromycin", "clarithromycin", "azithromycin"],
    "quinolone": ["quinolone", "ciprofloxacin", "levofloxacin", "moxifloxacin"],
    "tetracycline": ["tetracycline", "doxycycline", "minocycline"],
    "cephalosporin": ["cephalosporin", "cefalexin", "ceftriaxone", "cefuroxime"],
}

# A recorded penicillin allergy does not forbid a cephalosporin, but it does change the
# decision — cross-reactivity is low, not zero.
CROSS_REACTIVITY: dict[str, list[tuple[str, str]]] = {
    "penicillin": [
        ("cephalosporin", "possible cross-reactivity with a recorded penicillin allergy"),
    ],
    "aspirin": [
        ("nsaid", "NSAIDs can trigger the same reaction as aspirin, and aspirin-"
                  "exacerbated respiratory disease presents with wheeze"),
    ],
}

# Current medication -> (class of suggested drug, what happens).
INTERACTIONS: dict[str, list[tuple[str, str]]] = {
    "warfarin": [
        ("macrolide", "raises INR — bleeding risk; check INR within days if started"),
        ("quinolone", "raises INR — bleeding risk; check INR within days if started"),
        ("sulfonamide", "markedly raises INR — avoid where an alternative exists"),
        ("metronidazole", "markedly raises INR"),
        ("nsaid", "bleeding risk on top of anticoagulation"),
        ("aspirin", "bleeding risk on top of anticoagulation"),
    ],
    "theophylline": [
        ("quinolone", "raises theophylline levels — toxicity risk"),
        ("macrolide", "raises theophylline levels — toxicity risk"),
    ],
    "methotrexate": [
        ("sulfonamide", "raises methotrexate toxicity"),
        ("nsaid", "raises methotrexate levels"),
    ],
    "lisinopril": [
        ("nsaid", "reduces the antihypertensive effect and risks renal impairment"),
    ],
}

# Chronic condition (matched as a substring) -> (drug class or name, caution).
CONDITION_CAUTIONS: list[tuple[str, str, str]] = [
    ("asthma", "nsaid", "NSAIDs can precipitate bronchospasm in asthma"),
    ("asthma", "aspirin", "aspirin can precipitate bronchospasm in asthma"),
    ("asthma", "beta blocker", "non-selective beta blockers can precipitate bronchospasm"),
    ("copd", "beta blocker", "may worsen bronchospasm"),
    ("diabetes", "prednisolone", "systemic corticosteroids raise blood glucose — "
                                 "warn the patient and review monitoring"),
    ("diabetes", "dexamethasone", "systemic corticosteroids raise blood glucose"),
    ("heart failure", "nsaid", "NSAIDs cause fluid retention and worsen heart failure"),
    ("renal", "nsaid", "NSAIDs risk further renal impairment"),
    ("peptic ulcer", "nsaid", "NSAIDs risk gastrointestinal bleeding"),
]


# --------------------------------------------------------------------------------
# Screening
# --------------------------------------------------------------------------------


class MedicationWarning(_Strict):
    medication: str
    severity: WarningSeverity
    reason: str


class SuggestedMedication(_Strict):
    name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    rationale: Optional[str] = None

    def to_prescription(self) -> PrescribedMedication:
        return PrescribedMedication(
            name=self.name,
            dose=self.dose,
            frequency=self.frequency,
            duration=self.duration,
            rationale=self.rationale,
        )


class RawMedicationAdvice(_Strict):
    """Exactly what the model may return."""

    medications: list[SuggestedMedication] = []
    non_drug_advice: list[str] = []
    notes: list[str] = []


class MedicationAdvice(_Strict):
    """Screened advice: what may be offered, and what was taken away."""

    medications: list[SuggestedMedication] = []
    withheld: list[MedicationWarning] = []
    cautions: list[MedicationWarning] = []
    non_drug_advice: list[str] = []
    notes: list[str] = []

    def cautions_for(self, medication: SuggestedMedication) -> list[MedicationWarning]:
        return [w for w in self.cautions if w.medication == medication.name]


def _contains(haystack: str, needle: str) -> bool:
    return re.search(rf"\b{re.escape(needle)}", haystack, re.IGNORECASE) is not None


def drug_classes(name: str) -> set[str]:
    """Which classes a drug name belongs to."""
    lowered = name.lower()
    classes = {
        cls for cls, members in ALLERGY_CLASSES.items()
        if any(_contains(lowered, member) for member in members)
    }
    # Some tables key on a drug name that is not in ALLERGY_CLASSES at all.
    for extra in ("metronidazole", "beta blocker", "prednisolone", "dexamethasone"):
        if _contains(lowered, extra):
            classes.add(extra)
    return classes


def allergy_classes(profile: PatientProfile) -> dict[str, str]:
    """Map each recorded allergy to a known class. Returns class -> recorded substance."""
    found: dict[str, str] = {}
    for allergy in profile.allergies:
        substance = allergy.substance.lower()
        for cls, aliases in ALLERGY_ALIASES.items():
            if any(alias in substance for alias in aliases):
                found[cls] = allergy.substance
    return found


def screen(advice: RawMedicationAdvice, profile: PatientProfile) -> MedicationAdvice:
    """Remove what the patient is allergic to; flag what needs care."""
    patient_allergies = allergy_classes(profile)
    current = [m.name.lower() for m in profile.active_medications()]
    conditions = " ".join(c.name.lower() for c in profile.chronic_conditions)

    kept: list[SuggestedMedication] = []
    withheld: list[MedicationWarning] = []
    cautions: list[MedicationWarning] = []

    for medication in advice.medications:
        classes = drug_classes(medication.name)

        allergic_to = [
            (cls, substance)
            for cls, substance in patient_allergies.items()
            if cls in classes
        ]
        if allergic_to:
            cls, substance = allergic_to[0]
            withheld.append(
                MedicationWarning(
                    medication=medication.name,
                    severity="withheld",
                    reason=f"recorded allergy to {substance} — {medication.name} is a "
                           f"{cls} and must not be given",
                )
            )
            continue

        # Cross-reactivity with an allergy the patient does have.
        for allergic_cls, substance in patient_allergies.items():
            for related_cls, note in CROSS_REACTIVITY.get(allergic_cls, []):
                if related_cls in classes:
                    cautions.append(
                        MedicationWarning(
                            medication=medication.name,
                            severity="caution",
                            reason=f"{note} ({substance})",
                        )
                    )

        # Interactions with what the patient already takes.
        for existing in current:
            for drug, pairs in INTERACTIONS.items():
                if not _contains(existing, drug):
                    continue
                for related_cls, note in pairs:
                    if related_cls in classes:
                        cautions.append(
                            MedicationWarning(
                                medication=medication.name,
                                severity="caution",
                                reason=f"interacts with the patient's {drug} — {note}",
                            )
                        )

        # Chronic conditions that make the drug harder to use.
        for condition, target, note in CONDITION_CAUTIONS:
            if condition in conditions and target in classes | {medication.name.lower()}:
                cautions.append(
                    MedicationWarning(
                        medication=medication.name, severity="caution", reason=note
                    )
                )

        kept.append(medication)

    return MedicationAdvice(
        medications=kept,
        withheld=withheld,
        cautions=cautions,
        non_drug_advice=advice.non_drug_advice,
        notes=advice.notes,
    )


# --------------------------------------------------------------------------------
# Asking the model
# --------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are advising a physician on treatment options for a respiratory \
presentation. The physician prescribes; you propose.

You will be given the patient's clinical context, today's consultation, and the working \
diagnosis the physician has selected. Suggest the medications usually appropriate for \
that diagnosis in a primary care setting.

RULES
- The working diagnosis is the physician's decision. Do not argue with it. Suggest \
treatment for the diagnosis you were given.
- Read the allergies and current medications in the context. Never suggest a drug the \
patient is recorded as allergic to, and say in the rationale when an allergy or an \
interaction shaped your choice.
- Give a rationale for every drug: what it is for, and why this one.
- Include non-drug management (inhaler technique, smoking cessation, fluids, safety \
netting) in non_drug_advice — much of respiratory primary care is not a prescription.
- Doses are the physician's responsibility. Give usual adult doses where you are \
confident and leave the field null where you are not. Never invent a paediatric dose.
- Say nothing about likelihood or diagnosis here. That decision has been made.

OUTPUT FORMAT
Return ONLY a single valid JSON object, no markdown or commentary:

{
  "medications": [
    {"name": string, "dose": string | null, "frequency": string | null,
     "duration": string | null, "rationale": string}
  ],
  "non_drug_advice": [string],
  "notes": [string]
}

Every key must be present; lists may be empty. Return the JSON object now."""
