"""What the assistant is allowed to return, and how it is validated.

This module is the contract for a clinical assessment. It is deliberately free of any
LLM or framework imports — it describes the shape of an answer and enforces the rules
that answer must obey, nothing more. `chatbot/consultation.py` does the talking.

Two rules are enforced here in code rather than merely requested in the prompt, because
a prompt is a request and a validator is a guarantee:

1. **No numeric likelihood.** A model has no calibrated basis for "70% likely", but a
   clinician reads it as if it did. Percentages are rejected outright.
2. **Citations come from the retriever, not the model.** The model may only reference
   evidence by number; the source list is attached afterwards from what was actually
   retrieved. A fabricated reference has nowhere to land.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from clinical.json_reply import ReplyError, parse_model
from patient.visit import Diagnosis


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentError(ReplyError):
    """Raised when the model's reply cannot be parsed or fails the clinical rules."""


_PERCENT_RE = re.compile(r"%|percent", re.IGNORECASE)


class EvidenceSource(_Strict):
    """One retrieved excerpt, numbered so the model can point at it.

    `citation` and `section` come from the curated sources manifest, so a reference
    shown to the physician names the document it came from rather than a file name.
    """

    index: int
    source: str
    page: Optional[int] = None
    page_label: Optional[str] = None
    citation: Optional[str] = None
    section: Optional[str] = None
    snippet: Optional[str] = None

    def reference(self) -> str:
        """A human-readable citation for display."""
        parts = [self.citation or Path(self.source).name]
        if self.section:
            parts.append(self.section)
        page = self.page_label or (str(self.page) if self.page is not None else None)
        if page:
            parts.append(f"p.{page}")
        return " — ".join(parts)


class SuggestedDiagnosis(_Strict):
    """One entry in the differential. A suggestion — never a decision.

    `citations` holds indices into the evidence list supplied with the prompt.
    """

    label: str
    likelihood: Optional[str] = None
    reasoning: Optional[str] = None
    citations: list[int] = []

    @field_validator("likelihood")
    @classmethod
    def _no_numeric_likelihood(cls, value: Optional[str]) -> Optional[str]:
        if value and _PERCENT_RE.search(value):
            raise ValueError(
                "likelihood must be described in words (e.g. 'most likely', "
                "'possible', 'unlikely but must be excluded'), never as a percentage"
            )
        return value

    def to_diagnosis(self) -> Diagnosis:
        """Convert to the record type the doctor's chosen diagnosis is stored as.

        ICD-10 is left empty on purpose: coding is a separate step with its own
        validation, and a code guessed here would look authoritative without being so.
        """
        return Diagnosis(
            label=self.label,
            likelihood=self.likelihood,
            reasoning=self.reasoning,
        )


class RawAssessment(_Strict):
    """Exactly what the model is permitted to return — and nothing else.

    Note the absence of a `sources` field. The model cannot supply one, so it cannot
    invent a reference; `Assessment` gets its sources from the retriever instead.
    """

    differential: list[SuggestedDiagnosis] = []
    missing_information: list[str] = []
    concerning_features: list[str] = []
    context_factors: list[str] = []


class Assessment(_Strict):
    """A parsed, rule-checked assessment plus the evidence it was given."""

    differential: list[SuggestedDiagnosis] = []
    missing_information: list[str] = []
    concerning_features: list[str] = []
    context_factors: list[str] = []
    sources: list[EvidenceSource] = []

    def cited_sources(self, diagnosis: SuggestedDiagnosis) -> list[EvidenceSource]:
        by_index = {s.index: s for s in self.sources}
        return [by_index[i] for i in diagnosis.citations if i in by_index]


def parse_assessment(raw_text: str) -> RawAssessment:
    """Recover and validate the model's JSON reply."""
    return parse_model(raw_text, RawAssessment, AssessmentError)


def build_assessment(raw: RawAssessment, sources: list[EvidenceSource]) -> Assessment:
    """Attach the real evidence list and drop any citation that points nowhere.

    A model that cites [7] when six excerpts were supplied is not corrected or
    retried — the dangling reference is simply removed, so nothing on screen points
    at a source that does not exist.
    """
    valid = {s.index for s in sources}
    differential = [
        diagnosis.model_copy(
            update={"citations": [i for i in diagnosis.citations if i in valid]}
        )
        for diagnosis in raw.differential
    ]
    return Assessment(
        differential=differential,
        missing_information=raw.missing_information,
        concerning_features=raw.concerning_features,
        context_factors=raw.context_factors,
        sources=sources,
    )
