"""The safety boundary: what the model can and cannot do to a priority.

These are the tests that matter most in the triage agent, in the same way the allergy
screen tests matter most in `test_clinical_services.py`. They assert that a hostile,
broken or absent model cannot lower a patient's priority - not because the prompt asks
it not to, but because there is no path through the code that allows it.
"""

from __future__ import annotations

import json

import pytest

from triage import news2 as news2_engine
from triage import red_flags as red_flag_engine
from triage.decision import ai_ceiling, decide, rule_floor
from triage.interpret import bound, interpret
from triage.rules import get_rules
from triage.schema import (
    Interpretation,
    Priority,
    RawInterpretation,
    TriageRequest,
    TriageVitals,
)
from triage.service import triage
from triage.validation import validate


@pytest.fixture(scope="module")
def rules():
    return get_rules()


class FakeReply:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """Returns whatever it was constructed with, once per invoke."""

    def __init__(self, payload):
        self.payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return FakeReply(self.payload)


class BrokenLLM:
    def invoke(self, _messages):
        raise RuntimeError("429 rate limited")


def request(**kwargs) -> TriageRequest:
    vitals = kwargs.pop("vitals", None)
    return TriageRequest(
        age_years=kwargs.pop("age_years", 40),
        vitals=TriageVitals(**(vitals or {})),
        **kwargs,
    )


NORMAL = {
    "temperature_c": 36.8, "heart_rate": 76, "respiratory_rate": 16,
    "blood_pressure": "122/78", "spo2": 98, "on_oxygen": False, "consciousness": "alert",
}

CRITICAL_VITALS = {
    "temperature_c": 38.9, "heart_rate": 125, "respiratory_rate": 30,
    "blood_pressure": "100/60", "spo2": 84, "on_oxygen": False, "consciousness": "alert",
}


def run(req, rules, llm=None, rules_only=False):
    return triage(req, rules=rules, llm=llm, rules_only=rules_only)


# --------------------------------------------------------------------------------
# The model cannot lower a priority
# --------------------------------------------------------------------------------


def test_model_cannot_de_escalate_a_critical_patient(rules):
    """The whole design in one test.

    The model is handed a critically hypoxic patient and does everything it can to talk
    the priority down: it says the patient is fine, reports no risk signals, and sets
    escalate to false. The result must still be CRITICAL.
    """
    reassuring = FakeLLM(
        {
            "risk_signals": [],
            "escalate": False,
            "escalation_reason": None,
            "concerning_findings": [],
            "missing_information": [],
            "context_factors": ["patient looks well and is comfortable at rest"],
            "summary": "Low risk presentation, safe to wait.",
        }
    )
    result = run(request(chief_complaint="feels a bit breathless", vitals=CRITICAL_VITALS),
                 rules, llm=reassuring)

    assert result.priority == Priority.CRITICAL
    assert result.rule_priority == Priority.CRITICAL
    assert result.interpretation.available is True
    assert result.ai_escalated is False


def test_model_returning_a_priority_field_is_rejected_outright(rules):
    """`RawInterpretation` forbids extra keys, so a model that invents one fails closed.

    The interpretation becomes unavailable and the rules stand - which is the correct
    outcome, and is why the failure path had to be a degraded result rather than a
    crash.
    """
    overreaching = FakeLLM(
        {
            "risk_signals": [],
            "escalate": False,
            "escalation_reason": None,
            "concerning_findings": [],
            "missing_information": [],
            "context_factors": [],
            "summary": None,
            "priority": "LOW",
        }
    )
    result = run(request(chief_complaint="chest pain", vitals=NORMAL), rules, llm=overreaching)

    assert result.interpretation.available is False
    assert result.priority == Priority.CRITICAL  # the red flag still stands


def escalating_llm() -> FakeLLM:
    return FakeLLM(
        {
            "risk_signals": ["describes pressure on the chest with sweating"],
            "escalate": True,
            "escalation_reason": "Pressure-like chest discomfort with autonomic symptoms.",
            "concerning_findings": [],
            "missing_information": [],
            "context_factors": [],
            "summary": None,
        }
    )


def test_escalation_raises_a_low_patient_to_urgent(rules):
    """The phrase list has no entry for this wording; the model is what catches it."""
    result = run(
        request(chief_complaint="my chest feels like someone is sitting on it", vitals=NORMAL),
        rules, llm=escalating_llm(),
    )

    assert result.rule_priority == Priority.LOW
    assert result.priority == Priority.URGENT
    assert result.ai_escalated is True
    assert "escalated to URGENT" in " ".join(result.reasons)


def test_escalation_cannot_reach_critical_on_its_own(rules):
    """Only the deterministic rules or a clinician may declare a CRITICAL."""
    result = run(
        request(chief_complaint="feels unwell and something is wrong", vitals=NORMAL),
        rules, llm=escalating_llm(),
    )
    assert result.priority == Priority.URGENT
    assert result.priority != Priority.CRITICAL


def test_escalating_an_already_critical_patient_changes_nothing(rules):
    result = run(
        request(chief_complaint="crushing chest pain", vitals=NORMAL), rules, llm=escalating_llm()
    )
    assert result.priority == Priority.CRITICAL
    assert result.ai_escalated is False


def test_the_ceiling_comes_from_the_rule_file(rules):
    assert ai_ceiling(rules) == Priority.URGENT


def test_escalation_without_a_reason_is_dropped():
    """A model that wants a patient moved up has to say why."""
    interpretation = bound(
        RawInterpretation(escalate=True, escalation_reason=None), model="fake", latency_ms=1
    )
    assert interpretation.escalate is False
    assert interpretation.escalation_reason is None


# --------------------------------------------------------------------------------
# The agent works without the model
# --------------------------------------------------------------------------------


def test_unreachable_model_still_produces_a_complete_result(rules):
    result = run(request(chief_complaint="cough", vitals=CRITICAL_VITALS), rules, llm=BrokenLLM())

    assert result.priority == Priority.CRITICAL
    assert result.is_degraded() is True
    assert result.interpretation.failure is not None
    assert result.reasons  # a reason is still given
    assert result.recommended_action


def test_malformed_json_degrades_rather_than_raising(rules):
    result = run(request(chief_complaint="cough", vitals=NORMAL), rules, llm=FakeLLM("not json"))
    assert result.interpretation.available is False
    assert result.priority == Priority.LOW


def test_rules_only_mode_never_calls_the_model(rules):
    llm = FakeLLM({"risk_signals": [], "escalate": True, "escalation_reason": "x",
                   "concerning_findings": [], "missing_information": [],
                   "context_factors": [], "summary": None})
    result = run(request(chief_complaint="cough", vitals=NORMAL), rules, llm=llm, rules_only=True)

    assert llm.calls == 0
    assert result.interpretation.available is False
    assert result.priority == Priority.LOW


# --------------------------------------------------------------------------------
# The rule floor itself
# --------------------------------------------------------------------------------


def floor_for(req, rules):
    report = validate(req, rules)
    scores = news2_engine.score(report.normalised, rules, req.hypercapnic_target_range)
    hits = red_flag_engine.detect(req, rules)
    return rule_floor(scores, report, hits, rules)


def test_red_flag_beats_a_perfect_news2(rules):
    """NEWS2 0 with crushing chest pain is the case the red-flag layer exists for."""
    priority, reasons = floor_for(
        request(chief_complaint="crushing central chest pressure with sweating", vitals=NORMAL),
        rules,
    )
    assert priority == Priority.CRITICAL
    assert any("chest pain" in r.lower() for r in reasons)


def test_single_parameter_red_score_escalates_a_low_aggregate(rules):
    vitals = dict(NORMAL, respiratory_rate=26)
    priority, reasons = floor_for(request(chief_complaint="cough", vitals=vitals), rules)

    assert priority == Priority.URGENT
    assert any("red score" in r.lower() or "urgent review" in r.lower() for r in reasons)


def test_incomplete_observations_cannot_be_low(rules):
    req = request(chief_complaint="cough", vitals={"temperature_c": 37.2, "consciousness": "alert"})
    priority, reasons = floor_for(req, rules)

    assert priority == Priority.STANDARD
    assert any("incomplete" in r.lower() for r in reasons)


def test_out_of_scope_patient_is_escalated_not_scored(rules):
    result = run(request(age_years=7, chief_complaint="cough", vitals=NORMAL), rules, rules_only=True)

    assert result.status == "OUT_OF_SCOPE"
    assert result.priority == Priority.URGENT
    assert "16 and over" in " ".join(result.reasons)


def test_out_of_scope_still_honours_red_flags(rules):
    """A child with a threatened airway is CRITICAL, not merely out of scope."""
    result = run(
        request(age_years=4, chief_complaint="stridor and drooling", vitals=NORMAL),
        rules, rules_only=True,
    )
    assert result.status == "OUT_OF_SCOPE"
    assert result.priority == Priority.CRITICAL


def test_pregnant_patient_is_out_of_scope(rules):
    result = run(
        request(age_years=28, is_pregnant=True, chief_complaint="lightheaded", vitals=NORMAL),
        rules, rules_only=True,
    )
    assert result.status == "OUT_OF_SCOPE"
    assert result.priority == Priority.URGENT


def test_pregnancy_not_asked_is_not_treated_as_pregnant(rules):
    result = run(
        request(age_years=28, is_pregnant=None, chief_complaint="sore throat", vitals=NORMAL),
        rules, rules_only=True,
    )
    assert result.status == "OK"
    assert result.priority == Priority.LOW


# --------------------------------------------------------------------------------
# The result is explainable
# --------------------------------------------------------------------------------


def test_every_result_carries_its_reasoning_and_provenance(rules):
    result = run(request(chief_complaint="cough and fever",
                         vitals=dict(NORMAL, temperature_c=38.5, heart_rate=115,
                                     respiratory_rate=22)),
                 rules, rules_only=True)

    assert result.reasons
    assert result.recommended_action
    assert result.requires_human_review is True
    assert result.ruleset_version
    assert result.engine_version
    assert result.news2 is not None and result.news2.parameters


def test_result_serialises_to_json_for_a_backend(rules):
    from triage.service import triage_payload

    payload = triage_payload(
        {"age_years": 40, "chief_complaint": "cough", "vitals": NORMAL},
        rules=rules, rules_only=True,
    )
    assert payload["priority"] == "LOW"
    assert payload["requires_human_review"] is True
    json.dumps(payload)  # must be serialisable as-is
