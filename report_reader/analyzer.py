"""The second pass over an extracted report: what does it say?

`extractor.py` transcribes and interprets nothing — that guarantee is what makes the
stored JSON a faithful record of what was printed. This module is the separate step the
physician triggers afterwards, and it does not modify the extraction.

The split inside this module matters as much as the split from extraction:

**Flagging is arithmetic, done in code.** Whether 15.8 falls outside 4-11 is a
comparison, not a judgement, and a language model doing it will occasionally get it
wrong in a way nobody notices. Where the laboratory printed its own status or flag, that
wins over anything we compute — the lab knows its assay.

**Summarising is language, done by the model.** Turning a table of flagged values into
"neutrophil-predominant leucocytosis with a raised inflammatory marker" is what a model
is genuinely good at.

What neither does is decide what the report means for *this patient*. That reasoning
needs the whole chart and belongs to the doctor assistant, which sees the analysis when
the visit resumes. Two components reasoning clinically from different context is how you
get two confident answers and no way to choose between them.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from report_reader.schema import ExtractedReport, TestResult

Flag = Literal["low", "high", "abnormal", "normal", "unknown"]
FlagSource = Literal["reported", "computed", "none"]

# Laboratory flag letters, as printed next to a value.
_REPORTED_FLAGS: dict[str, Flag] = {
    "h": "high", "high": "high", "hh": "high",
    "l": "low", "low": "low", "ll": "low",
    "a": "abnormal", "abnormal": "abnormal", "*": "abnormal", "critical": "abnormal",
    "n": "normal", "normal": "normal",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlaggedResult(_Strict):
    parameter: str
    value: Optional[str] = None
    unit: Optional[str] = None
    reference: Optional[str] = None
    flag: Flag = "unknown"
    # Where the flag came from, so a physician can tell a printed "H" from our own
    # comparison against the printed range.
    source: FlagSource = "none"

    def is_abnormal(self) -> bool:
        return self.flag in {"low", "high", "abnormal"}

    def describe(self) -> str:
        parts = [f"{self.parameter}: {self.value if self.value is not None else '?'}"]
        if self.unit:
            parts.append(self.unit)
        line = " ".join(parts)
        if self.reference:
            line += f" (ref {self.reference})"
        if self.flag != "unknown":
            line += f" [{self.flag.upper()}"
            line += ", as printed]" if self.source == "reported" else "]"
        return line


class RawReportAnalysis(_Strict):
    """Exactly what the model may return."""

    summary: str = ""
    patterns: list[str] = []
    caveats: list[str] = []


class ReportAnalysis(_Strict):
    """The stored analysis: deterministic flags plus the model's reading of them."""

    summary: str = ""
    patterns: list[str] = []
    caveats: list[str] = []
    results: list[FlaggedResult] = []

    def abnormal(self) -> list[FlaggedResult]:
        return [r for r in self.results if r.is_abnormal()]

    def to_summary_text(self) -> str:
        """The block handed to the doctor assistant when the visit resumes."""
        lines = [self.summary] if self.summary else []
        abnormal = self.abnormal()
        if abnormal:
            lines.append("Abnormal values:")
            lines += [f"  - {r.describe()}" for r in abnormal]
        else:
            lines.append("No values fell outside their reference ranges.")
        lines += [f"  - {p}" for p in self.patterns]
        return "\n".join(lines)


# --------------------------------------------------------------------------------
# Deterministic flagging
# --------------------------------------------------------------------------------


def _as_number(value) -> Optional[float]:
    """A number, or None. Threshold and titre strings deliberately do not convert.

    "<0.01" is not 0.01 — it means the assay could not measure below its floor, and
    treating it as a number invites a false comparison against the range.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _reference_text(result: TestResult) -> Optional[str]:
    ref = result.reference_range
    if ref is None:
        return None
    if ref.text:
        return ref.text
    if ref.low is not None and ref.high is not None:
        return f"{ref.low} - {ref.high}"
    if ref.low is not None:
        return f"> {ref.low}"
    if ref.high is not None:
        return f"< {ref.high}"
    return None


def flag_result(result: TestResult) -> FlaggedResult:
    """Decide whether one value is out of range, preferring what the lab printed."""
    flagged = FlaggedResult(
        parameter=result.parameter,
        value=None if result.value is None else str(result.value),
        unit=result.unit,
        reference=_reference_text(result),
    )

    printed = (result.flags or result.status or "").strip().lower()
    if printed in _REPORTED_FLAGS:
        flagged.flag = _REPORTED_FLAGS[printed]
        flagged.source = "reported"
        return flagged

    value = _as_number(result.value)
    ref = result.reference_range
    if value is None or ref is None:
        return flagged

    low, high = _as_number(ref.low), _as_number(ref.high)
    if low is not None and value < low:
        flagged.flag, flagged.source = "low", "computed"
    elif high is not None and value > high:
        flagged.flag, flagged.source = "high", "computed"
    elif low is not None or high is not None:
        flagged.flag, flagged.source = "normal", "computed"
    return flagged


def flag_results(extracted: dict) -> list[FlaggedResult]:
    """Flag every result in an extracted report (as returned by extract_report)."""
    report = ExtractedReport.model_validate(extracted)
    return [flag_result(result) for result in report.results]


# --------------------------------------------------------------------------------
# The model's reading
# --------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are summarising a single laboratory or radiology report for a \
physician. You describe what the report says. You do not diagnose the patient, and you \
have not seen their history.

You will be given the report's identifying details and its results, each already marked \
LOW, HIGH, ABNORMAL or NORMAL. Those markings were computed from the printed reference \
ranges, or taken from the laboratory's own flags. Trust them.

WHAT TO PRODUCE
- summary: two to four sentences describing what this report shows. Name the abnormal \
values and the direction of the abnormality. If everything is within range, say so \
plainly.
- patterns: notable groupings a physician would recognise, e.g. "neutrophil-predominant \
leucocytosis with a raised inflammatory marker", "restrictive pattern", "isolated \
thrombocytopenia". Leave empty if there is no pattern worth naming.
- caveats: anything limiting the reading — values the extraction marked uncertain, a \
missing reference range, a result reported only as a threshold such as "<0.01", or a \
report that appears incomplete.

ABSOLUTE RULES
- NEVER give a diagnosis, a differential, or a cause. "Raised white cell count with \
neutrophilia" is a description; "consistent with bacterial infection" is a diagnosis, \
and it is not yours to make here.
- NEVER recommend treatment or further tests.
- NEVER state a value that is not in the results you were given, and never recalculate \
or convert one.
- NEVER decide a value is high or low yourself. Use the markings supplied.
- Do not speculate about the patient. You do not know their age, history, or symptoms.

OUTPUT FORMAT
Return ONLY a single valid JSON object, no markdown or commentary:

{"summary": string, "patterns": [string], "caveats": [string]}

Every key must be present; lists may be empty. Return the JSON object now."""


def build_analysis_prompt(extracted: dict, flagged: list[FlaggedResult]) -> str:
    report = ExtractedReport.model_validate(extracted)
    document = report.document
    header = [
        "=== REPORT ===",
        f"Type: {document.report_type or 'not stated'}",
        f"Laboratory: {document.laboratory_name or 'not stated'}",
        f"Report date: {document.report_date or 'not stated'}",
        "",
        "Results:",
    ]
    header += [f"  {result.describe()}" for result in flagged] or ["  (no results)"]

    if report.comments and report.comments.interpretation:
        header += ["", "Interpretation printed on the report (verbatim):",
                   f"  {report.comments.interpretation}"]
    if report.uncertain_fields:
        header += ["", f"Fields the extraction could not read: {', '.join(report.uncertain_fields)}"]

    header.append("=== END OF REPORT ===")
    return "\n".join(header)


def analyse(extracted: dict, llm=None) -> ReportAnalysis:
    """Flag the values in code, then ask the model to describe what they show."""
    # Imported here so the extraction pipeline can be used without the chat plumbing.
    from chatbot.consultation import run_structured

    flagged = flag_results(extracted)
    raw = run_structured(
        SYSTEM_PROMPT,
        [build_analysis_prompt(extracted, flagged)],
        RawReportAnalysis,
        llm=llm,
    )
    return ReportAnalysis(
        summary=raw.summary,
        patterns=raw.patterns,
        caveats=raw.caveats,
        results=flagged,
    )
