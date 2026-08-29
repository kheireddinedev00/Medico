"""Runs one clinical assessment.

    patient context + today's findings -> retrieve evidence -> model -> validated Assessment

This is the whole assistant, and it is a single function call with no memory. Every
request rebuilds the patient's context from the record, so two consecutive assessments
for the same patient are identical in what the model knows — there is no conversation
to drift, and nothing about a patient survives in the process after `assess` returns.

If the assistant asks for missing information, the physician adds it to the findings and
calls `assess` again. That re-read of the record is the point, not a limitation.

Nothing here writes to the database. Recording a decision is the physician's action and
belongs to the consultation state machine.
"""

from __future__ import annotations

from langchain_core.documents import Document

from chatbot.llm import get_llm, run_structured
from chatbot.prompt import SYSTEM_PROMPT
from clinical.differential import (
    Assessment,
    AssessmentError,
    EvidenceSource,
    RawAssessment,
    build_assessment,
)
from config import EVIDENCE_CHUNKS
from patient.history import ClinicalContext, format_findings
from patient.visit import Visit
from rag.retriever import get_retriever

# `run_structured` moved to chatbot/llm.py when the triage agent needed it too - it is
# model plumbing, not consultation logic. Re-exported here because clinical/session.py
# and the tests import it from this module, and moving a function is not a reason to
# make callers care.
__all__ = ["assess", "run_structured", "build_retrieval_query", "to_evidence",
           "format_evidence", "build_user_message"]

_SNIPPET_CHARS = 300


def build_retrieval_query(context: ClinicalContext, findings: Visit) -> str:
    """What to search the reference library for.

    Built from the presentation plus the patient's chronic conditions: searching for
    "cough fever" alone would miss the COPD material that matters for this particular
    patient. Deterministic — no model call decides what gets retrieved.
    """
    parts: list[str] = []
    if findings.chief_complaint:
        parts.append(findings.chief_complaint)
    parts.extend(findings.symptoms)
    if findings.physical_exam:
        parts.append(findings.physical_exam)
    parts.extend(c.name for c in context.profile.chronic_conditions)
    return " ".join(parts).strip()


def to_evidence(docs: list[Document]) -> list[EvidenceSource]:
    """Number the retrieved excerpts so the model can cite them.

    Prefers `pdf_page` — stamped at ingest from the curated manifest — and falls back
    to whatever page number the loader recorded for anything ingested before curation.
    """
    sources = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        page = meta.get("pdf_page", meta.get("page"))
        label = meta.get("page_label")
        sources.append(
            EvidenceSource(
                index=i,
                source=str(meta.get("source", "unknown")),
                page=int(page) if isinstance(page, (int, float)) else None,
                page_label=str(label) if label is not None else None,
                citation=meta.get("citation"),
                section=meta.get("section"),
                snippet=doc.page_content[:_SNIPPET_CHARS],
            )
        )
    return sources


def format_evidence(docs: list[Document]) -> str:
    if not docs:
        return (
            "=== RETRIEVED EVIDENCE ===\n"
            "No reference material was retrieved for this presentation. Reason from the "
            "clinical findings alone and leave all citations empty.\n"
            "=== END OF RETRIEVED EVIDENCE ==="
        )
    blocks = ["=== RETRIEVED EVIDENCE ==="]
    for i, doc in enumerate(docs, 1):
        blocks.append(f"[{i}] {doc.page_content}")
    blocks.append("=== END OF RETRIEVED EVIDENCE ===")
    return "\n\n".join(blocks)


def build_user_message(
    context: ClinicalContext, findings: Visit, docs: list[Document]
) -> str:
    return "\n\n".join(
        [
            context.to_prompt_text(),
            format_findings(findings),
            format_evidence(docs),
        ]
    )


def assess(
    context: ClinicalContext,
    findings: Visit,
    retriever=None,
    llm=None,
) -> Assessment:
    """Produce a differential for this consultation.

    `retriever` and `llm` are injectable so the flow can be exercised with fakes in a
    test without a network call.
    """
    retriever = retriever if retriever is not None else get_retriever(k=EVIDENCE_CHUNKS)

    query = build_retrieval_query(context, findings)
    docs: list[Document] = retriever.invoke(query) if query else []

    raw = run_structured(
        SYSTEM_PROMPT,
        [build_user_message(context, findings, docs)],
        RawAssessment,
        llm=llm,
        error_cls=AssessmentError,
    )
    return build_assessment(raw, to_evidence(docs))
