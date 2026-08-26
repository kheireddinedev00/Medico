"""Phase 6: report flagging, analysis storage, and results reaching the assistant."""

from __future__ import annotations

import json

import pytest

from clinical.session import ConsultationError, ConsultationSession
from patient.history import build_clinical_context
from patient.profile import PatientProfile
from patient.report import StoredReport
from patient.visit import Investigation, Visit
from report_reader.analyzer import (
    ReportAnalysis,
    RawReportAnalysis,
    analyse,
    build_analysis_prompt,
    flag_result,
    flag_results,
)
# Aliased: pytest tries to collect any imported name starting with "Test" as a test class.
from report_reader.schema import ReferenceRange, TestResult as LabResult
from storage import db
from storage.patient_repository import SqlitePatientRepository
from storage.report_repository import SqliteReportRepository
from storage.visit_repository import SqliteVisitRepository

CBC = {
    "document": {"report_type": "CBC", "report_date": "2026-08-10"},
    "patient": {"name": "Someone Else"},
    "results": [
        {"parameter": "WBC", "value": 15.8, "unit": "10^3/uL",
         "reference_range": {"low": 4, "high": 11}},
        {"parameter": "Haemoglobin", "value": 13.9, "unit": "g/dL",
         "reference_range": {"low": 13.5, "high": 17.5}},
        {"parameter": "Platelets", "value": 95, "unit": "10^3/uL",
         "reference_range": {"low": 150, "high": 400}},
        {"parameter": "Troponin", "value": "<0.01", "unit": "ng/mL",
         "reference_range": {"text": "<0.04"}},
    ],
    "uncertain_fields": [],
}


class FakeLLM:
    def __init__(self, reply=None):
        self.reply = reply or json.dumps(
            {"summary": "Raised white cell count with low platelets.",
             "patterns": ["leucocytosis with thrombocytopenia"],
             "caveats": ["Troponin reported only as a threshold."]}
        )
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append("\n".join(str(m.content) for m in messages))
        return type("Reply", (), {"content": self.reply})()


def result(**kwargs) -> LabResult:
    return LabResult(**kwargs)


# --- deterministic flagging ------------------------------------------------------


def test_a_value_above_its_range_is_high():
    flagged = flag_result(
        result(parameter="WBC", value=15.8, reference_range=ReferenceRange(low=4, high=11))
    )
    assert flagged.flag == "high" and flagged.source == "computed"


def test_a_value_below_its_range_is_low():
    flagged = flag_result(
        result(parameter="Platelets", value=95, reference_range=ReferenceRange(low=150, high=400))
    )
    assert flagged.flag == "low"


def test_a_value_inside_its_range_is_normal():
    flagged = flag_result(
        result(parameter="Hb", value=13.9, reference_range=ReferenceRange(low=13.5, high=17.5))
    )
    assert flagged.flag == "normal" and not flagged.is_abnormal()


def test_boundary_values_are_normal_not_abnormal():
    for value in (4, 11):
        flagged = flag_result(
            result(parameter="WBC", value=value, reference_range=ReferenceRange(low=4, high=11))
        )
        assert flagged.flag == "normal"


def test_a_threshold_value_is_not_treated_as_a_number():
    """'<0.01' does not mean 0.01 — comparing it to a range would be a false reading."""
    flagged = flag_result(
        result(parameter="Troponin", value="<0.01", reference_range=ReferenceRange(low=0, high=0.04))
    )
    assert flagged.flag == "unknown" and flagged.source == "none"


def test_no_reference_range_means_no_verdict():
    assert flag_result(result(parameter="Colour", value="Yellow")).flag == "unknown"


@pytest.mark.parametrize("printed,expected", [("H", "high"), ("L", "low"), ("*", "abnormal")])
def test_a_printed_laboratory_flag_wins_over_our_arithmetic(printed, expected):
    """The lab knows its own assay; we do not overrule its printed flag."""
    flagged = flag_result(
        result(parameter="WBC", value=5.0, flags=printed,
               reference_range=ReferenceRange(low=4, high=11))
    )
    assert flagged.flag == expected and flagged.source == "reported"


def test_the_flag_description_says_where_the_verdict_came_from():
    computed = flag_result(
        result(parameter="WBC", value=15.8, unit="10^3/uL",
               reference_range=ReferenceRange(low=4, high=11))
    )
    reported = flag_result(result(parameter="WBC", value=15.8, flags="H"))

    assert "[HIGH]" in computed.describe()
    assert "ref 4 - 11" in computed.describe()
    assert "as printed" in reported.describe()


def test_a_whole_report_is_flagged():
    flagged = flag_results(CBC)
    by_name = {f.parameter: f.flag for f in flagged}
    assert by_name == {
        "WBC": "high", "Haemoglobin": "normal", "Platelets": "low", "Troponin": "unknown"
    }


# --- the model's part ------------------------------------------------------------


def test_the_prompt_carries_the_flags_not_the_raw_values():
    prompt = build_analysis_prompt(CBC, flag_results(CBC))
    assert "[HIGH]" in prompt and "[LOW]" in prompt
    assert "CBC" in prompt


def test_analysis_combines_computed_flags_with_the_models_words():
    llm = FakeLLM()
    analysis = analyse(CBC, llm=llm)

    assert analysis.summary.startswith("Raised white cell count")
    assert [r.parameter for r in analysis.abnormal()] == ["WBC", "Platelets"]
    assert analysis.patterns == ["leucocytosis with thrombocytopenia"]


def test_a_model_that_returns_junk_does_not_silently_produce_an_empty_analysis():
    from clinical.json_reply import ReplyError

    with pytest.raises(ReplyError):
        analyse(CBC, llm=FakeLLM(reply="I think the patient has sepsis."))


def test_the_summary_text_lists_only_abnormal_values():
    analysis = analyse(CBC, llm=FakeLLM())
    text = analysis.to_summary_text()
    assert "WBC" in text and "Platelets" in text
    assert "Haemoglobin" not in text


def test_a_normal_report_says_so_rather_than_listing_nothing():
    analysis = ReportAnalysis(summary="All within range.", results=flag_results(
        {"document": {}, "patient": {}, "results": [
            {"parameter": "WBC", "value": 7.0, "reference_range": {"low": 4, "high": 11}}
        ], "uncertain_fields": []}
    ))
    assert "No values fell outside" in analysis.to_summary_text()


# --- storage ---------------------------------------------------------------------


@pytest.fixture
def repos(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    patients = SqlitePatientRepository(conn)
    patients.save(PatientProfile(id="P-1", full_name="Test Patient"))
    yield patients, SqliteVisitRepository(conn), SqliteReportRepository(conn)
    conn.close()


def test_a_report_round_trips_with_its_analysis(repos):
    _, _, reports = repos
    report = StoredReport(patient_id="P-1", report_type="CBC", extracted=CBC)
    reports.save(report)

    assert reports.get(report.id).is_analysed is False

    report.attach_analysis(analyse(CBC, llm=FakeLLM()))
    reports.save(report)

    reloaded = reports.get(report.id)
    assert reloaded.is_analysed
    assert reloaded.analysis.summary.startswith("Raised white cell count")
    assert [r.parameter for r in reloaded.analysis.abnormal()] == ["WBC", "Platelets"]


def test_the_extraction_is_never_altered_by_analysis(repos):
    """The transcription stays exactly as report_reader produced it."""
    _, _, reports = repos
    report = StoredReport(patient_id="P-1", extracted=CBC)
    report.attach_analysis(analyse(CBC, llm=FakeLLM()))
    reports.save(report)

    assert reports.get(report.id).extracted == CBC


def test_reports_can_be_listed_by_visit(repos):
    patients, visits, reports = repos
    visit = Visit(patient_id="P-1")
    visits.save(visit)
    reports.save(StoredReport(patient_id="P-1", visit_id=visit.id, report_type="CBC"))
    reports.save(StoredReport(patient_id="P-1", report_type="Unlinked"))

    assert [r.report_type for r in reports.list_for_visit(visit.id)] == ["CBC"]
    assert len(reports.list_for_patient("P-1")) == 2


# --- the clinical context --------------------------------------------------------


def analysed_report(**kwargs) -> StoredReport:
    report = StoredReport(patient_id="P-1", extracted=CBC, **kwargs)
    report.attach_analysis(analyse(CBC, llm=FakeLLM()))
    return report


def test_analysed_reports_reach_the_clinical_context():
    context = build_clinical_context(
        PatientProfile(id="P-1", full_name="T"), [],
        [analysed_report(report_type="CBC", kind="laboratory")],
    )
    text = context.to_prompt_text()

    assert "RECENT LABORATORY FINDINGS:" in text
    assert "Raised white cell count" in text
    assert "WBC: 15.8" in text
    assert "RECENT IMAGING FINDINGS: None recorded." in text


def test_an_unanalysed_report_is_listed_as_unanalysed_not_omitted():
    """'No imaging on file' and 'the film is sitting there unread' are different."""
    unread = StoredReport(patient_id="P-1", kind="radiology", report_type="Chest X-ray")
    text = build_clinical_context(
        PatientProfile(id="P-1", full_name="T"), [], [unread]
    ).to_prompt_text()

    assert "Chest X-ray" in text
    assert "not yet analysed" in text


def test_no_reports_reads_as_none_recorded():
    text = build_clinical_context(PatientProfile(id="P-1", full_name="T"), []).to_prompt_text()
    assert "RECENT LABORATORY FINDINGS: None recorded." in text


# --- feeding results into the consultation ---------------------------------------


def waiting_session(repos) -> ConsultationSession:
    patients, visits, reports = repos
    session = ConsultationSession.start(patients, visits, "P-1", reports)
    session.apply_findings(Visit(patient_id="P-1", chief_complaint="cough"))
    session.select_diagnosis("Community-acquired pneumonia")
    session.choose_investigation_path()
    session.order_investigations([Investigation(name="CBC", category="laboratory")])
    return session


def test_an_analysed_report_becomes_the_results(repos):
    patients, visits, reports = repos
    session = waiting_session(repos)
    report = analysed_report(visit_id=session.visit.id, report_type="CBC")
    reports.save(report)

    resumed = ConsultationSession.resume(patients, visits, session.visit.id, reports)
    assert [r.id for r in resumed.analysed_reports()] == [report.id]

    resumed.record_results_from_reports(resumed.analysed_reports())

    assert "WBC: 15.8" in resumed.visit.results_summary
    assert report.id in resumed.visit.report_ids
    assert resumed.visit.ordered_investigations[0].status == "resulted"


def test_an_unanalysed_report_cannot_be_used_as_results(repos):
    patients, visits, reports = repos
    session = waiting_session(repos)
    reports.save(StoredReport(patient_id="P-1", visit_id=session.visit.id, extracted=CBC))

    resumed = ConsultationSession.resume(patients, visits, session.visit.id, reports)
    assert resumed.analysed_reports() == []
    with pytest.raises(ConsultationError, match="analysed"):
        resumed.record_results_from_reports(resumed.pending_reports())


def test_this_visits_reports_are_not_also_shown_as_background(repos):
    patients, visits, reports = repos
    session = waiting_session(repos)
    reports.save(analysed_report(visit_id=session.visit.id, report_type="CBC"))

    resumed = ConsultationSession.resume(patients, visits, session.visit.id, reports)
    assert resumed.context.recent_reports == []


def test_results_from_reports_reach_the_assistant_prompt(repos):
    from patient.history import format_findings

    patients, visits, reports = repos
    session = waiting_session(repos)
    reports.save(analysed_report(visit_id=session.visit.id, report_type="CBC"))

    resumed = ConsultationSession.resume(patients, visits, session.visit.id, reports)
    resumed.record_results_from_reports(resumed.analysed_reports())

    prompt_text = format_findings(resumed.visit)
    assert "INVESTIGATION RESULTS" in prompt_text
    assert "WBC: 15.8" in prompt_text
