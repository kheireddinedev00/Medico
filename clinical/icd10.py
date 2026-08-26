"""ICD-10 code suggestions for the diagnosis the physician chose.

The module works at two levels of assurance, depending on whether
`data/icd10_respiratory.json` has been filled in.

**Without the list** (the default): codes are checked only for *shape* — that they look
like `J18.9`. A well-formed code that does not exist, or exists but means something
else, passes. A model producing a plausible wrong code is the realistic failure and
exactly the kind that survives a quick glance, so the output says so plainly.

**With the list**: the valid codes are put in front of the model rather than recalled by
it, anything not on the list is dropped, and the description shown to the physician is
taken from the list rather than from the model. Same principle as retrieved citations —
the model points, the code supplies the authoritative text.

What the list still cannot do is code correctly. Choosing J44.0 over J44.1 depends on
whether an infection is present, and that is a clinical judgement, not a lookup. Codes
remain suggestions for the physician to confirm either way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

from config import ICD10_CODE_LIST

# ICD-10: a letter, two digits, optionally a dot and up to four more characters.
# The leading letter deliberately includes U. Chapter XXII ("codes for special
# purposes") is where WHO put U07.1 for COVID-19, and a respiratory assistant that
# cannot express COVID-19 is missing one of the conditions it exists to consider.
CODE_RE = re.compile(r"^[A-Z]\d{2}(\.[0-9A-Z]{1,4})?$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestedCode(_Strict):
    code: str
    description: Optional[str] = None
    rationale: Optional[str] = None

    def is_well_formed(self) -> bool:
        return bool(CODE_RE.match(self.code.strip().upper()))


class CodeAdvice(_Strict):
    codes: list[SuggestedCode] = []
    notes: list[str] = []

    def well_formed(self) -> list[SuggestedCode]:
        """Only the codes that at least look like ICD-10.

        Malformed codes are dropped rather than shown, because a code is either usable
        or it is noise, and noise next to real codes makes the real ones less trusted.
        """
        return [c for c in self.codes if c.is_well_formed()]


# --------------------------------------------------------------------------------
# The optional curated code list
# --------------------------------------------------------------------------------


class CodeEntry(_Strict):
    code: str
    description: str
    # Extra words to match on that are not in the official description, e.g. "COVID"
    # for U07.1 or "flu" for J11.
    synonyms: list[str] = []


class CodeList(_Strict):
    """A curated subset of ICD-10, with its provenance."""

    title: Optional[str] = None
    publisher: Optional[str] = None
    edition: Optional[str] = None
    url: Optional[str] = None
    codes: list[CodeEntry] = []

    def __len__(self) -> int:
        return len(self.codes)

    @property
    def provenance(self) -> str:
        parts = [p for p in (self.publisher, self.edition, self.title) if p]
        return ", ".join(parts) if parts else "curated list"

    def lookup(self, code: str) -> Optional[CodeEntry]:
        wanted = code.strip().upper()
        return next((e for e in self.codes if e.code.upper() == wanted), None)

    def candidates(self, diagnosis: str, limit: int = 60) -> list[CodeEntry]:
        """The codes worth putting in front of the model for this diagnosis.

        A short list goes in whole — filtering it would only risk hiding the right
        answer. A long one is scored by how many words of the diagnosis appear in each
        entry, and the best `limit` are sent.
        """
        if len(self.codes) <= limit:
            return list(self.codes)

        words = {w for w in re.findall(r"[a-z]{4,}", diagnosis.lower())}
        if not words:
            return self.codes[:limit]

        def score(entry: CodeEntry) -> int:
            haystack = f"{entry.description} {' '.join(entry.synonyms)}".lower()
            return sum(1 for word in words if word in haystack)

        ranked = sorted(self.codes, key=score, reverse=True)
        return [e for e in ranked if score(e) > 0][:limit] or ranked[:limit]


class CodeListError(RuntimeError):
    """The code list exists but could not be read."""


def load_code_list(path: Optional[Path] = None) -> Optional[CodeList]:
    """Read the curated list, or None if it is absent or still empty.

    An empty `codes` array counts as absent so the shipped template does not silently
    switch the system into "validated" mode with nothing to validate against.
    """
    source = Path(path or ICD10_CODE_LIST)
    if not source.exists():
        return None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CodeListError(f"{source} is not valid JSON: {exc}") from exc

    # Keys beginning with "_" are comments. The file is meant to be edited by hand and
    # notes in it are more useful than strictness about them.
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    try:
        code_list = CodeList.model_validate(raw)
    except Exception as exc:
        raise CodeListError(f"{source} does not match the expected format:\n{exc}") from exc

    malformed = [e.code for e in code_list.codes if not CODE_RE.match(e.code.strip().upper())]
    if malformed:
        raise CodeListError(
            f"{source} contains entries that are not ICD-10 codes: {', '.join(malformed[:5])}"
        )
    return code_list if code_list.codes else None


def validate_against(advice: CodeAdvice, code_list: Optional[CodeList]) -> CodeAdvice:
    """Drop codes that are not on the list, and take descriptions from the list.

    With no list this is shape-checking only, which is what the printed warning says.
    """
    if code_list is None:
        return CodeAdvice(codes=advice.well_formed(), notes=advice.notes)

    kept: list[SuggestedCode] = []
    dropped: list[str] = []
    for suggestion in advice.well_formed():
        entry = code_list.lookup(suggestion.code)
        if entry is None:
            dropped.append(suggestion.code)
            continue
        kept.append(
            SuggestedCode(
                code=entry.code,
                # The list's wording wins. The model's rationale is kept, because that
                # is about this patient and the list has nothing to say about it.
                description=entry.description,
                rationale=suggestion.rationale,
            )
        )

    notes = list(advice.notes)
    if dropped:
        notes.append(
            f"Not offered because they are not in the curated list: {', '.join(dropped)}."
        )
    return CodeAdvice(codes=kept, notes=notes)


def candidates_block(code_list: Optional[CodeList], diagnosis: str) -> Optional[str]:
    """The block of allowed codes injected into the prompt."""
    if code_list is None:
        return None
    entries = code_list.candidates(diagnosis)
    lines = [
        "=== PERMITTED ICD-10 CODES ===",
        "Choose ONLY from this list. Do not suggest a code that does not appear here; "
        "if none fits, return an empty list and explain why in notes.",
    ]
    lines += [f"  {entry.code}  {entry.description}" for entry in entries]
    lines.append("=== END OF PERMITTED ICD-10 CODES ===")
    return "\n".join(lines)


SYSTEM_PROMPT = """You are suggesting ICD-10 codes to a physician who has already chosen \
a working diagnosis. You are not making or questioning that diagnosis.

You will be given the patient's clinical context, today's consultation, and the chosen \
working diagnosis.

RULES
- Suggest between one and four ICD-10 codes that could apply, most appropriate first.
- Use real ICD-10 codes in the standard format: a letter, two digits, and where \
applicable a dot and further characters, e.g. J18.9, J44.1, J45.901.
- Give the official-style description for each code and one line on why it fits this \
presentation.
- Where the choice depends on something not recorded — an identified organism, whether \
an exacerbation is present, laterality — say so in notes rather than guessing.
- If you are not confident a code exists, leave it out. Fewer correct codes are worth \
more than a longer list.
- Do not suggest treatment or investigations.

OUTPUT FORMAT
Return ONLY a single valid JSON object, no markdown or commentary:

{
  "codes": [
    {"code": string, "description": string, "rationale": string}
  ],
  "notes": [string]
}

Every key must be present; lists may be empty. Return the JSON object now."""
