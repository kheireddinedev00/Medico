"""What is allowed into the reference library, and what each piece of it is about.

Two jobs:

1. **Curation.** Every source must be declared in `data/references/sources.json` with a
   citation and the page ranges that actually contain clinical guidance. A file that is
   not declared is not ingested. This is deliberate: the WHO PAL implementation manual
   is 138 pages of which roughly 24 are clinical — the rest is advocacy, health-system
   assessment, training logistics and programme monitoring. Ingesting all of it would
   mean the assistant retrieves a paragraph about district coordinators when it asks
   about wheeze, and a retrieved irrelevance is worse than no evidence, because the
   model will try to use it.

2. **Labelling.** Each chunk is tagged with the conditions it mentions, so retrieval can
   later be spread deliberately across a differential instead of returning six chunks
   about the same disease.

Both are deterministic. Nothing here calls a model to decide what a document says.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

from config import SOURCES_MANIFEST

# Canonical condition -> the phrases that indicate it. Word boundaries matter: a bare
# "TB" must not match inside another word, and "flu" must not match "influenzae" as a
# bacterium name in a pneumonia aetiology list.
CONDITION_PATTERNS: dict[str, list[str]] = {
    "pneumonia": [r"pneumonia", r"pneumonic"],
    "tuberculosis": [r"tuberculosis", r"\bTB\b", r"mycobacterium"],
    "asthma": [r"asthma"],
    "copd": [r"\bCOPD\b", r"chronic obstructive"],
    "chronic_bronchitis": [r"chronic bronchitis"],
    "acute_bronchitis": [r"acute bronchitis", r"\bbronchitis\b"],
    "upper_respiratory_infection": [
        r"upper respiratory",
        r"common cold",
        r"nasopharyngitis",
        r"rhinopharyngitis",
    ],
    "pharyngitis": [r"pharyngitis", r"tonsillitis", r"sore throat"],
    "sinusitis": [r"sinusitis"],
    "allergic_rhinitis": [r"allergic rhinitis", r"hay fever"],
    "influenza": [r"influenza(?!e)", r"\bflu\b"],
    "covid_19": [r"COVID", r"SARS-CoV-2"],
    "pleural_effusion": [r"pleural effusion", r"pleurisy", r"pleural"],
    "pulmonary_embolism": [r"pulmonary embolism", r"thromboembolism", r"embolus"],
    "lung_cancer": [r"lung cancer", r"bronchial carcinoma", r"neoplasm"],
}

_COMPILED = {
    condition: [re.compile(p, re.IGNORECASE) for p in patterns]
    for condition, patterns in CONDITION_PATTERNS.items()
}


class ManifestError(RuntimeError):
    """Raised when the sources manifest is missing or malformed."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Inclusive 1-based PDF page range, as a human reading the PDF would count them.
    first_page: int
    last_page: int

    def contains(self, pdf_page: int) -> bool:
        return self.first_page <= pdf_page <= self.last_page


class SourceSpec(BaseModel):
    """One curated document."""

    model_config = ConfigDict(extra="forbid")

    file: str
    title: str
    publisher: Optional[str] = None
    year: Optional[int] = None
    reference: Optional[str] = None
    # Where to obtain the file. Required in practice for copyrighted sources, which are
    # gitignored rather than committed — without this a fresh clone has a manifest
    # entry naming a document nobody can find.
    url: Optional[str] = None
    # Sections listed here are the ONLY parts that get ingested. Anything not covered
    # by a section is dropped, so exclusion is the default rather than an afterthought.
    sections: list[Section] = []
    notes: Optional[str] = None

    @property
    def citation(self) -> str:
        parts = [p for p in (self.publisher, str(self.year) if self.year else None) if p]
        prefix = f"{', '.join(parts)}. " if parts else ""
        suffix = f" ({self.reference})" if self.reference else ""
        return f"{prefix}{self.title}{suffix}"

    def section_for(self, pdf_page: int) -> Optional[Section]:
        for section in self.sections:
            if section.contains(pdf_page):
                return section
        return None

    def total_pages_declared(self) -> int:
        return sum(s.last_page - s.first_page + 1 for s in self.sections)


def load_manifest(path: Optional[Path] = None) -> dict[str, SourceSpec]:
    """Read sources.json, keyed by file name."""
    source = Path(path or SOURCES_MANIFEST)
    if not source.exists():
        raise ManifestError(
            f"No sources manifest at {source}. Every reference document must be declared "
            "there before it can be ingested."
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"sources.json is not valid JSON: {exc}") from exc

    specs = [SourceSpec.model_validate(entry) for entry in raw.get("sources", [])]
    return {spec.file: spec for spec in specs}


def tag_conditions(text: str) -> list[str]:
    """Which respiratory conditions this text mentions."""
    return sorted(
        condition
        for condition, patterns in _COMPILED.items()
        if any(p.search(text) for p in patterns)
    )


def conditions_to_metadata(conditions: list[str]) -> str:
    """Chroma metadata values must be scalars, so the tag list is stored as a string.

    Wrapped in commas at both ends so a substring test for ",asthma," cannot match
    another tag that merely starts with the same letters.
    """
    return "," + ",".join(conditions) + "," if conditions else ""
