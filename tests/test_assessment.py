"""Phase 2: parsing the assistant's reply, and the assessment flow.

No network calls. The model and the retriever are both fakes, so these tests check our
side of the contract: what we send, what we accept, and what we refuse.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.documents import Document

from chatbot.consultation import assess, build_retrieval_query, format_evidence, to_evidence
from clinical.differential import (
    AssessmentError,
    EvidenceSource,
    RawAssessment,
    SuggestedDiagnosis,
    build_assessment,
    parse_assessment,
)
from patient.history import build_clinical_context
from patient.profile import Allergy, ChronicCondition, PatientProfile
from patient.visit import Visit, Vitals

VALID_REPLY = {
    "differential": [
        {
            "label": "Community-acquired pneumonia",
            "likelihood": "most likely",
            "reasoning": "Focal crackles with fever and hypoxia.",
            "citations": [1],
        }
    ],
    "missing_information": ["Duration of fever"],
    "concerning_features": ["SpO2 92% on room air"],
    "context_factors": ["45 pack-year history"],
}


class FakeLLM:
    """Returns queued replies and records what it was asked."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "{}"
        return type("Reply", (), {"content": reply})()

    @property
    def last_prompt(self) -> str:
        return "\n".join(str(m.content) for m in self.calls[-1])


class FakeRetriever:
    def __init__(self, docs):
        self.docs = docs
        self.queries: list[str] = []

    def invoke(self, query):
        self.queries.append(query)
        return self.docs


@pytest.fixture
def docs():
    return [
        Document(page_content="Pneumonia presents with fever and crackles.",
                 metadata={"source": "ref.pdf", "page": 3}),
        Document(page_content="COPD exacerbation increases sputum purulence.",
                 metadata={"source": "ref.pdf", "page": 7}),
    ]


@pytest.fixture
def context():
    profile = PatientProfile(
        id="P-1",
        full_name="Test Patient",
        sex="male",
        age_years=68,
        allergies=[Allergy(substance="Penicillin", severity="severe")],
        chronic_conditions=[ChronicCondition(name="COPD")],
    )
    return build_clinical_context(profile, [])


@pytest.fixture
def findings():
    return Visit(
        patient_id="P-1",
        chief_complaint="Cough with fever",
        symptoms=["productive cough"],
        vitals=Vitals(temperature_c=38.6, spo2=92),
        physical_exam="Crackles at the left base",
    )


# --- parsing ---------------------------------------------------------------------


def test_parses_a_clean_json_reply():
    parsed = parse_assessment(json.dumps(VALID_REPLY))
    assert parsed.differential[0].label == "Community-acquired pneumonia"
    assert parsed.concerning_features == ["SpO2 92% on room air"]


def test_parses_a_reply_wrapped_in_code_fences():
    raw = "```json\n" + json.dumps(VALID_REPLY) + "\n```"
    assert parse_assessment(raw).differential[0].likelihood == "most likely"


def test_parses_a_reply_padded_with_prose():
    raw = "Here is my assessment:\n" + json.dumps(VALID_REPLY) + "\nHope this helps."
    assert len(parse_assessment(raw).differential) == 1


def test_missing_optional_keys_default_to_empty():
    parsed = parse_assessment('{"differential": []}')
    assert parsed.missing_information == [] and parsed.concerning_features == []


def test_non_json_reply_raises():
    with pytest.raises(AssessmentError):
        parse_assessment("I'm sorry, I can't help with that.")


def test_unknown_key_in_reply_raises():
    with pytest.raises(AssessmentError):
        parse_assessment('{"differential": [], "treatment_plan": "amoxicillin"}')


# --- the rules we enforce in code ------------------------------------------------


@pytest.mark.parametrize("likelihood", ["70%", "probability 0.7 (70 percent)", "85 %"])
def test_percentage_likelihood_is_refused(likelihood):
    reply = {"differential": [{"label": "Pneumonia", "likelihood": likelihood}]}
    with pytest.raises(AssessmentError):
        parse_assessment(json.dumps(reply))


def test_wordy_likelihood_is_accepted():
    reply = {"differential": [{"label": "Pneumonia", "likelihood": "most likely"}]}
    assert parse_assessment(json.dumps(reply)).differential[0].likelihood == "most likely"


def test_citations_pointing_at_nothing_are_dropped():
    raw = RawAssessment(
        differential=[SuggestedDiagnosis(label="Pneumonia", citations=[1, 7, 99])]
    )
    sources = [EvidenceSource(index=1, source="ref.pdf")]
    assessment = build_assessment(raw, sources)
    assert assessment.differential[0].citations == [1]


def test_cited_sources_resolves_to_real_documents():
    raw = RawAssessment(differential=[SuggestedDiagnosis(label="Pneumonia", citations=[2])])
    sources = [
        EvidenceSource(index=1, source="a.pdf"),
        EvidenceSource(index=2, source="b.pdf", page=7),
    ]
    assessment = build_assessment(raw, sources)
    cited = assessment.cited_sources(assessment.differential[0])
    assert [s.source for s in cited] == ["b.pdf"] and cited[0].page == 7


# --- retrieval and prompt assembly -----------------------------------------------


def test_retrieval_query_includes_findings_and_chronic_conditions(context, findings):
    query = build_retrieval_query(context, findings)
    assert "Cough with fever" in query
    assert "productive cough" in query
    assert "COPD" in query


def test_evidence_is_numbered_from_one(docs):
    sources = to_evidence(docs)
    assert [s.index for s in sources] == [1, 2]
    assert sources[1].page == 7
    assert "[2]" in format_evidence(docs)


def test_empty_evidence_tells_the_model_to_leave_citations_empty():
    assert "leave all citations empty" in format_evidence([])


# --- the whole flow --------------------------------------------------------------


def test_assessment_prompt_carries_the_record_and_the_findings(context, findings, docs):
    llm = FakeLLM(json.dumps(VALID_REPLY))
    assess(context, findings, retriever=FakeRetriever(docs), llm=llm)

    prompt = llm.last_prompt
    assert "Penicillin" in prompt, "the allergy must reach the model"
    assert "COPD" in prompt
    assert "Crackles at the left base" in prompt
    assert "Pneumonia presents with fever" in prompt, "retrieved evidence must reach the model"


def test_assessment_attaches_real_sources(context, findings, docs):
    llm = FakeLLM(json.dumps(VALID_REPLY))
    assessment = assess(context, findings, retriever=FakeRetriever(docs), llm=llm)

    assert [s.source for s in assessment.sources] == ["ref.pdf", "ref.pdf"]
    assert assessment.differential[0].citations == [1]


def test_a_malformed_first_reply_is_retried_once(context, findings, docs):
    llm = FakeLLM("sorry, no JSON here", json.dumps(VALID_REPLY))
    assessment = assess(context, findings, retriever=FakeRetriever(docs), llm=llm)

    assert len(llm.calls) == 2
    assert assessment.differential[0].label == "Community-acquired pneumonia"


def test_two_malformed_replies_give_up_rather_than_guess(context, findings, docs):
    llm = FakeLLM("no json", "still no json")
    with pytest.raises(AssessmentError):
        assess(context, findings, retriever=FakeRetriever(docs), llm=llm)
