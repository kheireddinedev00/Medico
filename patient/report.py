"""A report in the patient's record.

Three things are kept, and the order matters:

1. `extracted` — the transcription, exactly as report_reader produced it. This is the
   canonical clinical data. It is written once at upload and never modified.
2. `analysis` — the interpretation, produced later by an explicit physician action. It
   is stored beside the transcription, never merged into it, so a value that was read
   off the page stays distinguishable from a statement about that value.
3. `source_file` — where it came from, for the physician who wants to look at the
   original image.

A report belongs to a patient. It may also belong to the visit that ordered it, but it
does not have to: results arrive late, out of order, and sometimes for nothing anyone
ordered.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from report_reader.analyzer import ReportAnalysis

ReportKind = Literal["laboratory", "radiology", "other"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StoredReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"r-{uuid.uuid4().hex[:12]}")
    patient_id: str
    visit_id: Optional[str] = None
    kind: ReportKind = "laboratory"
    # As printed on the report ("CBC", "Chest X-ray"), not inferred.
    report_type: Optional[str] = None
    report_date: Optional[str] = None
    source_file: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=_now)

    extracted: dict = Field(default_factory=dict)

    analysis: Optional[ReportAnalysis] = None
    analysed_at: Optional[datetime] = None

    @property
    def is_analysed(self) -> bool:
        return self.analysis is not None

    def label(self) -> str:
        parts = [self.report_type or self.kind]
        if self.report_date:
            parts.append(self.report_date)
        return " — ".join(parts)

    def attach_analysis(self, analysis: ReportAnalysis) -> None:
        self.analysis = analysis
        self.analysed_at = _now()
