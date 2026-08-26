"""Investigation suggestions.

The doctor has chosen a working diagnosis and wants to know what would confirm or
refute it. Each suggestion carries a rationale, because "order a CRP" without a reason
is not decision support — the reason is the part the physician judges.

The category is normalised in code rather than trusted from the model, so an ordered
investigation always lands in one of the three buckets the record understands.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from patient.visit import Investigation

# What the model tends to write -> the category the record uses.
_CATEGORY_HINTS: dict[str, list[str]] = {
    "radiology": [
        "x-ray", "xray", "radiograph", "ct", "hrct", "ultrasound", "mri", "imaging",
        "scan", "echocardiogram", "echo",
    ],
    "laboratory": [
        "cbc", "fbc", "blood", "crp", "esr", "abg", "procalcitonin", "d-dimer", "culture",
        "sputum", "serology", "pcr", "swab", "urea", "electrolyte", "glucose", "inr",
        "troponin", "smear",
    ],
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestedInvestigation(_Strict):
    name: str
    category: Optional[str] = None
    rationale: Optional[str] = None
    # What the physician should do differently depending on the answer. A test whose
    # result changes nothing is a test worth not ordering.
    changes_management: Optional[str] = None

    def to_investigation(self) -> Investigation:
        return Investigation(
            name=self.name,
            category=normalise_category(self.name, self.category),
            rationale=self.rationale,
        )


class InvestigationAdvice(_Strict):
    investigations: list[SuggestedInvestigation] = []
    notes: list[str] = []


def normalise_category(name: str, stated: Optional[str]) -> str:
    """Decide the record category from the model's guess and the test's name."""
    if stated:
        lowered = stated.strip().lower()
        if lowered in {"laboratory", "lab"}:
            return "laboratory"
        if lowered in {"radiology", "imaging"}:
            return "radiology"

    haystack = f"{name} {stated or ''}".lower()
    for category, hints in _CATEGORY_HINTS.items():
        if any(hint in haystack for hint in hints):
            return category
    return "other"


SYSTEM_PROMPT = """You are advising a physician on which investigations would help in a \
respiratory presentation. The physician decides what to order; you explain what each \
test would tell them.

You will be given the patient's clinical context, today's consultation, and the working \
diagnosis the physician has selected.

RULES
- Suggest investigations that are realistic in primary care or a first-referral \
hospital. Do not suggest tests that need a facility this setting will not have.
- For each one, give the rationale, and say what it would change. If a result would not \
change management, do not suggest the test.
- Consider the patient's history: an anticoagulated patient needs different monitoring, \
a diabetic patient on metformin needs care around contrast imaging, and a long smoking \
history raises the threshold for imaging.
- Do not repeat an investigation the record shows has already been done in this visit \
unless repeating it is the point, and say so if it is.
- Do not suggest treatment here, and do not revisit the diagnosis.
- Between three and six suggestions is usually right. Fewer is fine.

OUTPUT FORMAT
Return ONLY a single valid JSON object, no markdown or commentary:

{
  "investigations": [
    {"name": string, "category": "laboratory" | "radiology" | "other",
     "rationale": string, "changes_management": string}
  ],
  "notes": [string]
}

Every key must be present; lists may be empty. Return the JSON object now."""
