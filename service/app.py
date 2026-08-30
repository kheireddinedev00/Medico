"""The clinical engine, over HTTP.

Read this first, because it is the one decision the rest of the project rests on:

**This service is stateless and stores nothing.** Laravel owns the database and is the
only thing that writes to it. Every request carries the chart the engine needs; every
response carries what the engine produced. There is no SQLite file behind this process and
no session held between calls.

**All clinical logic stays on this side.** Laravel does not decide which state a visit may
move to, which drug is withheld for an allergy, or which ICD-10 code is valid. It asks, and
it stores the answer. The moment any of that is reimplemented in PHP there are two answers
and no way to choose between them.

The two rules together are what the routes below are shaped by. A transition route builds a
`ConsultationSession` over in-memory repositories, calls exactly one existing method, and
returns the mutated visit plus a `persist` flag. A suggestion route calls one existing
function and returns its validated output. Neither writes anything anywhere.

Run it:

    venv/Scripts/python -m uvicorn service.app:app --reload --port 8001

Interactive docs at /docs — useful for whoever is writing the Laravel client, and worth
showing at the defence.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

import config  # noqa: F401  — imported for its load_dotenv() side effect
from clinical import icd10
from clinical import investigations as investigation_advice
from clinical import medications
from clinical.consultation_state import (
    TRANSITIONS,
    InvalidTransition,
    VisitStatus,
    next_states,
)
from clinical.differential import AssessmentError
from clinical.json_reply import ReplyError
from clinical.session import ConsultationError, ConsultationSession
from patient.report import StoredReport
from patient.visit import Visit
from report_reader.analyzer import analyse
from report_reader.extractor import ExtractionError, extract_report
from report_reader.loader import UnsupportedFileError
from service.repositories import InMemoryPatients, InMemoryReports, InMemoryVisits
from service.schemas import (
    AnalyseRequest,
    Chart,
    ChartRequest,
    CodeRequest,
    DiagnosisRequest,
    FindingsRequest,
    FollowUpRequest,
    InvestigationsRequest,
    MedicationCheckRequest,
    NoteRequest,
    PrescribeRequest,
    ResultsFromReportsRequest,
    ResultsRequest,
    SoapRequest,
    StartRequest,
    TriageAssessRequest,
    VisitResult,
)
from soap.note import build_soap_note
from triage.rules import RuleSetError, get_rules
from triage.schema import RANK
from triage.service import TriageInputError
from triage.service import triage as run_triage

# Set SERVICE_API_KEY in .env and send it as X-Service-Key. This service answers clinical
# questions about named patients and must not be reachable from anywhere but the backend;
# a shared secret is the floor, not the ceiling — bind it to localhost as well.
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")

app = FastAPI(
    title="Respiratory CDSS engine",
    description=(
        "Stateless clinical engine. Suggests, validates and screens; never persists, "
        "never decides. The caller owns the record."
    ),
    version="1.0.0",
)


def require_key(x_service_key: Optional[str] = Header(default=None)) -> None:
    """Reject callers without the shared secret, unless no secret is configured."""
    if not SERVICE_API_KEY:
        return
    if x_service_key != SERVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Service-Key.")


# --------------------------------------------------------------------------------
# Error mapping
# --------------------------------------------------------------------------------


@app.exception_handler(InvalidTransition)
async def _invalid_transition(_request, exc: InvalidTransition):
    """409: the request was well formed, the workflow just does not allow it."""
    return JSONResponse(
        status_code=409, content={"error": "invalid_transition", "detail": str(exc)}
    )


@app.exception_handler(ConsultationError)
async def _consultation_error(_request, exc: ConsultationError):
    return JSONResponse(
        status_code=409, content={"error": "consultation_error", "detail": str(exc)}
    )


@app.exception_handler(AssessmentError)
async def _assessment_error(_request, exc: AssessmentError):
    """502: the model failed us, not the caller. Retrying the same request may work."""
    return JSONResponse(
        status_code=502, content={"error": "assessment_failed", "detail": str(exc)}
    )


@app.exception_handler(ReplyError)
async def _reply_error(_request, exc: ReplyError):
    return JSONResponse(
        status_code=502, content={"error": "model_reply_invalid", "detail": str(exc)}
    )


@app.exception_handler(icd10.CodeListError)
async def _code_list_error(_request, exc: icd10.CodeListError):
    return JSONResponse(
        status_code=500, content={"error": "code_list_invalid", "detail": str(exc)}
    )


@app.exception_handler(RuleSetError)
async def _rule_set_error(_request, exc: RuleSetError):
    """503, not 500: the service is up but cannot triage safely.

    There is deliberately no fallback. Every other degraded path in this system still
    produces a usable answer, but a rule set that will not load has no safe default -
    sorting patients on thresholds nobody reviewed is the one failure worth refusing.
    """
    return JSONResponse(
        status_code=503, content={"error": "triage_rules_unavailable", "detail": str(exc)}
    )


@app.exception_handler(TriageInputError)
async def _triage_input_error(_request, exc: TriageInputError):
    return JSONResponse(
        status_code=422, content={"error": "triage_input_invalid", "detail": str(exc)}
    )


# --------------------------------------------------------------------------------
# Building a session out of a request
# --------------------------------------------------------------------------------


def session_for(chart: Chart) -> ConsultationSession:
    """Rebuild the consultation the caller is working on.

    `resume` is used rather than `start` even for a brand-new visit, because the visit
    already exists in the payload and `resume` is what loads one. It also gives us its
    guards for free: a completed visit is refused, and the visit being worked on is kept
    out of its own background context.

    `persisted` has to be stated rather than assumed. `resume` normally assumes the visit
    came out of a database and is therefore already committed, which is true of every
    caller that loaded it from one. This service was handed the visit by Laravel and has no
    idea whether it was ever stored — so it derives the answer the same way the engine
    does: a visit joins the record when a diagnosis is chosen, and `select_diagnosis` is
    the only thing that sets `working_diagnosis`.
    """
    patients = InMemoryPatients([chart.profile])
    visits = InMemoryVisits([chart.visit, *chart.history])
    reports = InMemoryReports(chart.reports)
    return ConsultationSession.resume(
        patients=patients,
        visits=visits,
        visit_id=chart.visit.id,
        reports=reports,
        persisted=chart.visit.working_diagnosis is not None,
    )


def _transition(chart: Chart, action: Callable[[ConsultationSession], None]) -> VisitResult:
    """Run one decision and hand the resulting visit back for the caller to store."""
    session = session_for(chart)
    action(session)
    # `is_persisted` is the engine's answer to "is this visit part of the record yet?".
    # Reading it here is what keeps that rule out of PHP — see VisitResult.persist.
    return VisitResult(visit=session.visit, persist=session.is_persisted)


# --------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------


@app.get("/health", tags=["service"])
def health() -> dict:
    """Liveness, plus the facts that most often explain a surprising answer."""
    code_list = icd10.load_code_list()
    body = {
        "status": "ok",
        "model": config.OPENROUTER_MODEL,
        "vlm_model": config.VLM_MODEL,
        # Which of the two assurance levels ICD-10 suggestions are running at.
        "icd10_mode": "validated" if code_list else "shape-checked",
        "icd10_codes": len(code_list.codes) if code_list else 0,
        "api_key_configured": bool(config.OPENROUTER_API_KEY),
    }

    # The triage rule set is reported here rather than only at /reference/triage-rules,
    # because "the queue stopped scoring people" is something an operator needs to see on
    # the health check, not discover from a screen full of unscored patients.
    try:
        rules = get_rules()
        body["triage"] = {
            "status": "ok",
            "ruleset_version": rules.ruleset_version,
            "thresholds_verified": rules.is_verified(),
            "red_flags": len(rules.red_flags.flags),
        }
    except RuleSetError as exc:
        body["status"] = "degraded"
        body["triage"] = {"status": "unavailable", "detail": str(exc)}

    return body


# --------------------------------------------------------------------------------
# Reference data — for Laravel to seed from, read-only
# --------------------------------------------------------------------------------


@app.get("/reference/icd10", tags=["reference"], dependencies=[Depends(require_key)])
def reference_icd10() -> dict:
    """The curated code list, so Laravel can seed its own lookup table from one source."""
    code_list = icd10.load_code_list()
    if code_list is None:
        return {"mode": "shape-checked", "codes": []}
    return {
        "mode": "validated",
        "codes": [entry.model_dump() for entry in code_list.codes],
    }


@app.post("/triage/assess", tags=["triage"], dependencies=[Depends(require_key)])
def triage_assess(payload: TriageAssessRequest) -> dict:
    """Triage one patient. Stateless, like everything else here.

    Always returns a decision. The model is an enrichment that may be unavailable, and
    when it is, `interpretation.available` is false and the priority still stands on the
    deterministic layers - so the caller never has to handle "triage failed", only
    "triage ran without the model". The one thing that fails loudly is a broken rule
    set, because sorting patients on thresholds nobody reviewed is worse than not
    sorting them.

    `priority_rank` in the response is the sort key. Order by it descending, then by
    arrival time ascending, and the queue is correct without PHP knowing what CRITICAL
    means.
    """
    result = run_triage(
        payload.request, profile=payload.profile, rules_only=payload.rules_only
    )
    return result.model_dump(mode="json")


@app.get("/reference/triage-rules", tags=["reference"], dependencies=[Depends(require_key)])
def reference_triage_rules() -> dict:
    """The triage rule set, so the UI can explain a priority without a second copy of it.

    Served for the same reason the workflow is: a red-flag list or a priority ladder
    retyped into React is one that will disagree with the engine, and the disagreement
    surfaces as a screen telling a nurse something the system did not decide.
    """
    rules = get_rules()
    return {
        "ruleset_version": rules.ruleset_version,
        "verified": rules.is_verified(),
        "verification_status": rules.verification_status,
        "source": rules.news2.source.citation(),
        "population": {
            "min_age_years": rules.population.min_age_years,
            "excludes_pregnancy": rules.population.excludes_pregnancy,
        },
        # The ladder, so the front end sorts and colours from the engine's own ordering.
        "priorities": [
            {"priority": p.value, "rank": r}
            for p, r in sorted(RANK.items(), key=lambda kv: -kv[1])
        ],
        "actions": {
            band.priority.value: band.recommended_action for band in rules.escalation.bands
        },
        "retriage_interval_minutes": {
            p.value: m for p, m in rules.waiting_room.retriage_interval_minutes.items()
        },
        "red_flags": [
            {"id": f.id, "label": f.label, "floor": f.floor.value, "source": f.source}
            for f in rules.red_flags.flags
        ],
    }


@app.get("/reference/workflow", tags=["reference"], dependencies=[Depends(require_key)])
def reference_workflow() -> dict:
    """The state machine, so the front end can grey out what is not allowed.

    Served rather than hard-coded in React on purpose: a transition table copied into the
    UI is a transition table that will disagree with the engine eventually, and the
    disagreement will be discovered by a doctor rather than by a test.
    """
    return {
        "states": [s.value for s in VisitStatus],
        "transitions": {
            state.value: sorted(t.value for t in targets)
            for state, targets in TRANSITIONS.items()
        },
    }


# --------------------------------------------------------------------------------
# Consultation — the state machine. No model involved in any of these.
# --------------------------------------------------------------------------------


@app.post("/consultation/start", tags=["consultation"], dependencies=[Depends(require_key)])
def start(request: StartRequest) -> VisitResult:
    """Open a consultation. Returns an unsaved visit — note `persist: false`.

    Nothing is stored until the physician commits to a diagnosis, so a doctor who opens a
    patient, reads the assessment and closes the tab leaves no record behind.
    """
    patients = InMemoryPatients([request.profile])
    visits = InMemoryVisits(request.history)
    reports = InMemoryReports(request.reports)
    session = ConsultationSession.start(
        patients=patients,
        visits=visits,
        patient_id=request.profile.id,
        reports=reports,
    )
    return VisitResult(visit=session.visit, persist=session.is_persisted)


@app.post("/consultation/reset", tags=["consultation"], dependencies=[Depends(require_key)])
def reset(request: ChartRequest) -> VisitResult:
    """Empty a consultation and start it again from the beginning.

    Not a transition, and deliberately not expressed as one. The state machine describes how
    an encounter *progresses*; there is no edge back to the start because clinically there is
    no such move — a visit does not un-happen. This is an erase: the same visit id, emptied,
    as though it had just been opened.

    It exists because a consultation started on the wrong patient, or built on findings that
    turned out to belong to someone else, is better wiped than carried forward. The caller
    gets `persist: false` back, which is the same answer a freshly opened visit gives — so
    nothing is written until a diagnosis is chosen again, and a reset the doctor walks away
    from leaves nothing behind.

    What it does not do is hide the erase. The record above this service keeps the audit
    entry; what returns here is simply an empty visit.
    """
    visit = request.chart.visit

    return VisitResult(
        visit=Visit(
            id=visit.id,
            patient_id=visit.patient_id,
            created_at=visit.created_at,
        ),
        persist=False,
    )


@app.post("/consultation/findings", tags=["consultation"], dependencies=[Depends(require_key)])
def findings(request: FindingsRequest) -> VisitResult:
    """Record what the doctor observed."""
    return _transition(request.chart, lambda s: s.apply_findings(request.findings))


@app.post(
    "/consultation/select-diagnosis", tags=["consultation"], dependencies=[Depends(require_key)]
)
def select_diagnosis(request: DiagnosisRequest) -> VisitResult:
    """The physician's first real decision, and the point the record begins."""
    return _transition(
        request.chart,
        lambda s: s.select_diagnosis(
            label=request.label, icd10_code=request.icd10_code, reasoning=request.reasoning
        ),
    )


@app.post("/consultation/icd10-code", tags=["consultation"], dependencies=[Depends(require_key)])
def set_icd10_code(request: CodeRequest) -> VisitResult:
    """Attach the code the physician picked. Never called by the assistant."""
    return _transition(request.chart, lambda s: s.set_icd10_code(request.code))


@app.post(
    "/consultation/investigate", tags=["consultation"], dependencies=[Depends(require_key)]
)
def choose_investigation_path(request: ChartRequest) -> VisitResult:
    """Option A — investigations before treatment."""
    return _transition(request.chart, lambda s: s.choose_investigation_path())


@app.post(
    "/consultation/order-investigations",
    tags=["consultation"],
    dependencies=[Depends(require_key)],
)
def order_investigations(request: InvestigationsRequest) -> VisitResult:
    """Confirm the ordered tests. They accumulate; a second round never erases the first.

    Categories are normalised here rather than trusted from the caller. A test typed by
    hand arrives with no category, and a chest X-ray filed as "other" is simply wrong in
    the record — it will not appear under imaging on the timeline and will not read as a
    radiology result later. `normalise_category` already does this for the assistant's own
    suggestions; running it on everything keeps one rule instead of two.
    """
    normalised = [
        investigation.model_copy(
            update={
                "category": investigation_advice.normalise_category(
                    investigation.name, investigation.category
                )
            }
        )
        for investigation in request.investigations
    ]
    return _transition(request.chart, lambda s: s.order_investigations(normalised))


@app.post(
    "/consultation/skip-investigations",
    tags=["consultation"],
    dependencies=[Depends(require_key)],
)
def skip_investigations(request: ChartRequest) -> VisitResult:
    """Option B, or a change of mind after opening the list."""
    return _transition(request.chart, lambda s: s.skip_investigations())


@app.post(
    "/consultation/record-results", tags=["consultation"], dependencies=[Depends(require_key)]
)
def record_results(request: ResultsRequest) -> VisitResult:
    """Typed results. For uploaded reports use /consultation/record-results-from-reports."""
    return _transition(
        request.chart, lambda s: s.record_results(request.summary, resulted=request.resulted)
    )


@app.post(
    "/consultation/record-results-from-reports",
    tags=["consultation"],
    dependencies=[Depends(require_key)],
)
def record_results_from_reports(request: ResultsFromReportsRequest) -> VisitResult:
    """Bring analysed reports into the visit.

    Only reports the physician has actually run the analysis on are usable — an uploaded
    transcription contributes nothing until someone asks what it means.
    """
    chart = request.chart
    by_id = {r.id: r for r in chart.reports}
    missing = [rid for rid in request.report_ids if rid not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Reports not present in the chart payload: {', '.join(missing)}",
        )
    selected: list[StoredReport] = [by_id[rid] for rid in request.report_ids]
    return _transition(
        chart, lambda s: s.record_results_from_reports(selected, resulted=request.resulted)
    )


@app.post(
    "/consultation/amend-results", tags=["consultation"], dependencies=[Depends(require_key)]
)
def amend_results(request: ResultsRequest) -> VisitResult:
    """Correct what the recorded results say. Not a transition — the visit does not move.

    Recording appends, so results arriving separately accumulate. This replaces, for fixing
    a typo or tidying several runs of machine output into something a colleague can read.
    """
    return _transition(request.chart, lambda s: s.amend_results(request.summary))


@app.post(
    "/consultation/revise-diagnosis", tags=["consultation"], dependencies=[Depends(require_key)]
)
def revise_diagnosis(request: DiagnosisRequest) -> VisitResult:
    """Change the working diagnosis in light of results. Only valid at RESULTS_REVIEW."""
    return _transition(
        request.chart,
        lambda s: s.revise_diagnosis(
            label=request.label, icd10_code=request.icd10_code, reasoning=request.reasoning
        ),
    )


@app.post(
    "/consultation/order-more-investigations",
    tags=["consultation"],
    dependencies=[Depends(require_key)],
)
def order_more_investigations(request: ChartRequest) -> VisitResult:
    """Results raised a new question — back to test selection, same visit."""
    return _transition(request.chart, lambda s: s.order_more_investigations())


@app.post("/consultation/treat", tags=["consultation"], dependencies=[Depends(require_key)])
def proceed_to_treatment(request: ChartRequest) -> VisitResult:
    return _transition(request.chart, lambda s: s.proceed_to_treatment())


@app.post("/consultation/prescribe", tags=["consultation"], dependencies=[Depends(require_key)])
def prescribe(request: PrescribeRequest) -> VisitResult:
    """Record what the physician prescribed — which is not necessarily what was suggested."""
    return _transition(request.chart, lambda s: s.prescribe(request.medications))


@app.post("/consultation/follow-up", tags=["consultation"], dependencies=[Depends(require_key)])
def schedule_follow_up(request: FollowUpRequest) -> VisitResult:
    return _transition(request.chart, lambda s: s.schedule_follow_up(request.plan))


@app.post("/consultation/complete", tags=["consultation"], dependencies=[Depends(require_key)])
def complete(request: ChartRequest) -> VisitResult:
    """Terminal. A finished visit is not edited; a new concern is a new visit."""
    return _transition(request.chart, lambda s: s.complete())


@app.post("/consultation/note", tags=["consultation"], dependencies=[Depends(require_key)])
def add_note(request: NoteRequest) -> VisitResult:
    return _transition(request.chart, lambda s: s.add_note(request.note))


# --------------------------------------------------------------------------------
# The assistant — suggestions only. None of these writes, and none of them decides.
# --------------------------------------------------------------------------------


@app.post("/assistant/assess", tags=["assistant"], dependencies=[Depends(require_key)])
def assistant_assess(request: ChartRequest) -> dict:
    """A differential for today's findings, with citations from what was retrieved.

    The visit comes back too: the suggestion is recorded on it, so the caller stores the
    assistant's proposal alongside the physician's eventual decision rather than instead
    of it.
    """
    session = session_for(request.chart)
    assessment = session.assess()
    return {
        "assessment": assessment.model_dump(mode="json"),
        "visit": session.visit.model_dump(mode="json"),
        "persist": session.is_persisted,
    }


@app.post("/assistant/icd10", tags=["assistant"], dependencies=[Depends(require_key)])
def assistant_icd10(request: ChartRequest) -> dict:
    """Candidate codes for the chosen diagnosis, validated against the curated list."""
    return session_for(request.chart).suggest_codes().model_dump(mode="json")


@app.post("/assistant/investigations", tags=["assistant"], dependencies=[Depends(require_key)])
def assistant_investigations(request: ChartRequest) -> dict:
    """What would confirm or refute the working diagnosis, and why."""
    return session_for(request.chart).suggest_investigations().model_dump(mode="json")


@app.post("/assistant/medications", tags=["assistant"], dependencies=[Depends(require_key)])
def assistant_medications(request: ChartRequest) -> dict:
    """Treatment options, already screened against the patient's record.

    The screen runs inside the session, so there is no route here — and no route anywhere —
    that can return unscreened suggestions. Withheld drugs come back in their own field
    with the reason, and must be shown, not hidden: a withheld drug that leaves no trace is
    indistinguishable from one the model never suggested.
    """
    return session_for(request.chart).suggest_medications().model_dump(mode="json")


@app.post("/assistant/check-medications", tags=["assistant"], dependencies=[Depends(require_key)])
def check_medications(request: MedicationCheckRequest) -> dict:
    """Screen drugs the physician named themselves.

    No model is involved: this is the same code screen that filters the assistant's own
    suggestions, pointed at a list someone typed. Reusing it rather than writing a second
    check is the point — two implementations of "is this patient allergic to this" would
    eventually disagree, and the disagreement would surface as a drug handed over.

    Unlike the suggestion route, nothing is removed. A physician's choice is not overruled
    by a screen; they are told what it found and decide.
    """
    session = session_for(request.chart)
    raw = medications.RawMedicationAdvice(
        medications=[medications.SuggestedMedication(name=name) for name in request.names]
    )
    screened = medications.screen(raw, session.context.profile)

    return {
        # Named `blocked` rather than `withheld`: the screen withholds a suggestion, but it
        # only flags a physician's own decision.
        "blocked": [w.model_dump(mode="json") for w in screened.withheld],
        "cautions": [w.model_dump(mode="json") for w in screened.cautions],
    }


# --------------------------------------------------------------------------------
# Reports — extraction and analysis stay separate, here as everywhere
# --------------------------------------------------------------------------------


@app.post("/reports/extract", tags=["reports"], dependencies=[Depends(require_key)])
async def reports_extract(file: UploadFile = File(...)) -> dict:
    """Transcribe an uploaded report. Interprets nothing.

    The result is the canonical clinical data and is meant to be written once and never
    modified. Analysis is a separate call the physician triggers.
    """
    suffix = Path(file.filename or "").suffix
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)
        return extract_report(tmp_path)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # The upload is Laravel's to store. This process keeps no copy.
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.post("/reports/analyze", tags=["reports"], dependencies=[Depends(require_key)])
def reports_analyze(request: AnalyseRequest) -> dict:
    """Interpret a stored extraction — the physician's explicit second step.

    Out-of-range flagging is arithmetic done in code, and a flag the laboratory printed
    beats anything computed here. The model only writes the prose.
    """
    return analyse(request.extracted).model_dump(mode="json")


# --------------------------------------------------------------------------------
# SOAP — a projection of the record. No model, nothing stored.
# --------------------------------------------------------------------------------


@app.post("/soap", tags=["soap"], dependencies=[Depends(require_key)])
def soap(request: SoapRequest) -> dict:
    """Build the note for one visit.

    Rebuilt on request rather than stored, so it cannot go stale against the chart. There
    is no note id to save and no note table to migrate.
    """
    chart = request.chart
    note = build_soap_note(
        profile=chart.profile,
        visit=chart.visit,
        history=chart.history,
        reports=chart.reports,
        include_assistant_differential=request.include_assistant_differential,
    )
    return note.model_dump(mode="json")


# --------------------------------------------------------------------------------
# Introspection, for the front end
# --------------------------------------------------------------------------------


@app.post("/consultation/next-states", tags=["consultation"], dependencies=[Depends(require_key)])
def allowed_next_states(request: ChartRequest) -> dict:
    """Where this visit may go from here. Lets the UI disable what the engine would refuse."""
    return {
        "status": request.chart.visit.status.value,
        "next": sorted(s.value for s in next_states(request.chart.visit.status)),
    }
