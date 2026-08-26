"""Pydantic schema for a structured medical-report extraction.

Design notes:
- `value` and reference bounds are deliberately Union[number | str | None]. Real lab
  results are NOT always clean numbers: titers ("1:160"), thresholds ("<0.01", ">1000"),
  and qualitative results ("Negative", "Trace", "Yellow") must be preserved verbatim.
  Forcing float() here would silently corrupt medical data.
- Everything is Optional and defaults to None so a missing field is represented as null,
  never invented.
- extra="forbid" makes validation strict: if the model emits an unexpected key, we find
  out instead of silently passing it through.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# A lab value or range bound that may be numeric or a preserved string token.
Scalar = Union[float, int, str, None]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(_Strict):
    report_type: Optional[str] = None
    laboratory_name: Optional[str] = None
    report_date: Optional[str] = None
    collection_date: Optional[str] = None
    accession_number: Optional[str] = None
    report_id: Optional[str] = None


class Patient(_Strict):
    name: Optional[str] = None
    id: Optional[str] = None
    age: Optional[Union[int, str]] = None  # str allows "45 years", "6 months"
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None


class ReferenceRange(_Strict):
    low: Scalar = None
    high: Scalar = None
    # For non-numeric ranges printed as text, e.g. "Negative", "<150", "Non-reactive".
    text: Optional[str] = None


class TestResult(_Strict):
    parameter: str
    value: Scalar = None
    unit: Optional[str] = None
    reference_range: Optional[ReferenceRange] = None
    # Only when explicitly indicated on the report. Not inferred from value vs range.
    status: Optional[str] = None
    # Verbatim laboratory flag(s) as printed, e.g. "H", "L", "*", "A".
    flags: Optional[str] = None


class Comments(_Strict):
    physician_comments: Optional[str] = None
    laboratory_comments: Optional[str] = None
    notes: Optional[str] = None
    # Interpretation section captured as plain extracted text only — never summarized.
    interpretation: Optional[str] = None


class ExtractedReport(_Strict):
    document: Document = Field(default_factory=Document)
    patient: Patient = Field(default_factory=Patient)
    results: list[TestResult] = Field(default_factory=list)
    comments: Optional[Comments] = None
    # Dotted paths to fields that were present but unreadable, e.g.
    # "results[3].value" or "patient.date_of_birth".
    uncertain_fields: list[str] = Field(default_factory=list)
