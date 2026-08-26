"""Drives one consultation from first findings to a closed record.

This is where the doctor's decisions become the medical record. Two boundaries are
enforced here and are worth stating plainly:

**The assistant never writes.** `chatbot.consultation.assess` reads the record and
returns a suggestion; it has no repository and no way to persist anything. Every write
in this system happens in this module, and only in response to a method the doctor's
interface called. If a diagnosis is in the database, a human put it there.

**Suggestion and decision are stored separately.** `visit.differential` holds what the
assistant proposed; `visit.working_diagnosis` holds what the doctor chose. They are
allowed to disagree, and the record keeps both without complaint. That is the audit
trail for "the AI is not making the decisions here".

The session lives in `clinical/` rather than in the CLI because the web backend will
need this exact workflow. Logic that lives in a terminal interface gets reimplemented,
differently, by whoever builds the second interface.
"""

from __future__ import annotations

from typing import Optional

from chatbot.consultation import assess as run_assessment
from chatbot.consultation import run_structured
from clinical import icd10, investigations as investigation_advice, medications
from clinical.consultation_state import VisitStatus, require_transition
from clinical.differential import Assessment
from patient.history import ClinicalContext, build_clinical_context, format_findings
from patient.report import StoredReport
from patient.visit import Diagnosis, Investigation, PrescribedMedication, Visit
from storage.repository import PatientRepository, ReportRepository, VisitRepository


class ConsultationError(RuntimeError):
    """Raised when a consultation cannot be started or resumed."""


class ConsultationSession:
    """One encounter, in progress."""

    def __init__(
        self,
        patients: PatientRepository,
        visits: VisitRepository,
        context: ClinicalContext,
        visit: Visit,
        persisted: bool,
        reports: Optional[ReportRepository] = None,
    ):
        self._patients = patients
        self._visits = visits
        self._reports = reports
        self.context = context
        self.visit = visit
        # False until the doctor makes a real decision. Running an assessment and
        # walking away must not leave a record behind.
        self._persisted = persisted

    @property
    def is_persisted(self) -> bool:
        """Whether this visit belongs to the record yet.

        False until the physician commits to a diagnosis. A caller that owns the database
        itself — rather than writing through the repositories — reads this to decide
        whether to store the visit, so the rule stays here and is not restated elsewhere.
        """
        return self._persisted

    # --- construction ------------------------------------------------------------

    @classmethod
    def start(
        cls,
        patients: PatientRepository,
        visits: VisitRepository,
        patient_id: str,
        reports: Optional[ReportRepository] = None,
    ) -> "ConsultationSession":
        profile = patients.get(patient_id)
        if profile is None:
            raise ConsultationError(f"No patient with id {patient_id}")
        history = visits.list_for_patient(patient_id)
        return cls(
            patients=patients,
            visits=visits,
            reports=reports,
            context=build_clinical_context(
                profile, history, cls._reports_for(reports, patient_id)
            ),
            visit=Visit(patient_id=patient_id),
            persisted=False,
        )

    @classmethod
    def resume(
        cls,
        patients: PatientRepository,
        visits: VisitRepository,
        visit_id: str,
        reports: Optional[ReportRepository] = None,
        persisted: Optional[bool] = None,
    ) -> "ConsultationSession":
        """Continue an existing consultation.

        `persisted` says whether this visit is already part of the record. Leaving it None
        means yes, which is right for every caller that loaded the visit out of a database:
        nothing gets saved before a diagnosis is chosen, so anything on disk is committed.

        A stateless caller rebuilds the session on every request from a visit handed to it,
        and for that caller the assumption does not hold — the visit may never have been
        stored. It passes the answer explicitly rather than letting an abandoned assessment
        be mistaken for a record.
        """
        visit = visits.get(visit_id)
        if visit is None:
            raise ConsultationError(f"No visit with id {visit_id}")
        if visit.status is VisitStatus.COMPLETED:
            raise ConsultationError(
                f"Visit {visit_id} is completed. A new concern is a new visit."
            )
        profile = patients.get(visit.patient_id)
        if profile is None:
            raise ConsultationError(f"No patient with id {visit.patient_id}")

        # The visit being worked on is today's consultation, not part of the history —
        # without this it would appear twice in the prompt, once as context and once as
        # the presentation. Its own reports are excluded for the same reason: they
        # belong to the results block, not to the background.
        history = [v for v in visits.list_for_patient(visit.patient_id) if v.id != visit.id]
        background = [
            r for r in cls._reports_for(reports, visit.patient_id) if r.visit_id != visit.id
        ]
        return cls(
            patients=patients,
            visits=visits,
            reports=reports,
            context=build_clinical_context(profile, history, background),
            visit=visit,
            persisted=True if persisted is None else persisted,
        )

    @staticmethod
    def _reports_for(reports: Optional[ReportRepository], patient_id: str) -> list[StoredReport]:
        return list(reports.list_for_patient(patient_id)) if reports is not None else []

    # --- reports attached to this visit ------------------------------------------

    def pending_reports(self) -> list[StoredReport]:
        """Reports uploaded against this visit, analysed or not."""
        if self._reports is None:
            return []
        return list(self._reports.list_for_visit(self.visit.id))

    def analysed_reports(self) -> list[StoredReport]:
        return [r for r in self.pending_reports() if r.is_analysed]

    def record_results_from_reports(
        self, reports: list[StoredReport], resulted: Optional[list[str]] = None
    ) -> None:
        """Move to results review using stored analyses instead of typed text.

        Only analysed reports are used. An uploaded report nobody ran the analysis on
        contributes nothing here — the physician's explicit "analyze" action is what
        turns a transcription into something the assistant is allowed to reason over.
        """
        usable = [r for r in reports if r.is_analysed]
        if not usable:
            raise ConsultationError(
                "None of those reports have been analysed. Run the analysis first: "
                "python -m report_reader.cli analyze <report-id>"
            )
        summary = "\n\n".join(
            f"{report.label()}\n{report.analysis.to_summary_text()}" for report in usable
        )
        self.visit.report_ids = sorted({*self.visit.report_ids, *(r.id for r in usable)})
        self.record_results(summary, resulted=resulted)

    # --- the assistant (read-only) -----------------------------------------------

    def apply_findings(self, findings: Visit) -> None:
        """Copy what the doctor recorded onto this consultation."""
        self.visit.chief_complaint = findings.chief_complaint or self.visit.chief_complaint
        self.visit.symptoms = findings.symptoms or self.visit.symptoms
        if findings.vitals.model_dump(exclude_none=True):
            self.visit.vitals = findings.vitals
        self.visit.physical_exam = findings.physical_exam or self.visit.physical_exam
        self.visit.observations = findings.observations or self.visit.observations
        self._persist()

    def assess(self, retriever=None, llm=None) -> Assessment:
        """Ask the assistant for a differential. Records the suggestion, decides nothing."""
        assessment = run_assessment(self.context, self.visit, retriever=retriever, llm=llm)
        self.visit.differential = [d.to_diagnosis() for d in assessment.differential]
        self._persist()
        return assessment

    # --- the assistant's other suggestions (all read-only) -----------------------

    def _advice_blocks(self) -> list[str]:
        """The three things every clinical service is shown."""
        diagnosis = self.visit.working_diagnosis
        if diagnosis is None:
            raise ConsultationError(
                "The physician must choose a working diagnosis before the assistant can "
                "advise on codes, investigations or treatment."
            )
        code = f" [{diagnosis.icd10_code}]" if diagnosis.icd10_code else ""
        differential = ", ".join(d.label for d in self.visit.differential)
        block = [
            "=== WORKING DIAGNOSIS (chosen by the physician — do not question it) ===",
            f"{diagnosis.label}{code}",
        ]
        if differential:
            block.append(f"Other conditions considered: {differential}")
        block.append("=== END OF WORKING DIAGNOSIS ===")
        return [
            self.context.to_prompt_text(),
            format_findings(self.visit),
            "\n".join(block),
        ]

    def suggest_codes(self, llm=None) -> icd10.CodeAdvice:
        """Candidate ICD-10 codes, validated against the curated list when there is one.

        With a list, the permitted codes go into the prompt and the reply is filtered
        against them. Without one, this is shape-checking — see clinical/icd10.py.
        """
        blocks = self._advice_blocks()
        code_list = icd10.load_code_list()
        permitted = icd10.candidates_block(
            code_list, self.visit.working_diagnosis.label
        )
        if permitted:
            blocks.append(permitted)

        advice = run_structured(icd10.SYSTEM_PROMPT, blocks, icd10.CodeAdvice, llm=llm)
        return icd10.validate_against(advice, code_list)

    def suggest_investigations(self, llm=None) -> investigation_advice.InvestigationAdvice:
        return run_structured(
            investigation_advice.SYSTEM_PROMPT,
            self._advice_blocks(),
            investigation_advice.InvestigationAdvice,
            llm=llm,
        )

    def suggest_medications(self, llm=None) -> medications.MedicationAdvice:
        """Treatment options, screened against the patient's record before returning.

        The screen runs here rather than in the caller so that no interface — CLI, web
        backend, or anything later — can obtain unscreened suggestions by accident.
        """
        raw = run_structured(
            medications.SYSTEM_PROMPT,
            self._advice_blocks(),
            medications.RawMedicationAdvice,
            llm=llm,
        )
        return medications.screen(raw, self.context.profile)

    # --- the doctor's decisions --------------------------------------------------

    def select_diagnosis(
        self, label: str, icd10_code: Optional[str] = None, reasoning: Optional[str] = None
    ) -> None:
        """The first real decision, and the point at which the record is created."""
        label = (label or "").strip()
        if not label:
            raise ConsultationError("A working diagnosis needs a label.")
        self._advance(VisitStatus.ICD10_SELECTION)
        self.visit.working_diagnosis = Diagnosis(
            label=label, icd10_code=icd10_code, reasoning=reasoning
        )
        self._persisted = True
        self._persist()

    def set_icd10_code(self, code: str) -> None:
        """Attach a code the physician picked. Never called by the assistant itself."""
        if self.visit.working_diagnosis is None:
            raise ConsultationError("There is no working diagnosis to code.")
        if self.visit.status is not VisitStatus.ICD10_SELECTION:
            raise ConsultationError(
                f"Coding happens at ICD10_SELECTION, not at {self.visit.status.value}."
            )
        self.visit.working_diagnosis.icd10_code = code.strip().upper() or None
        self._persist()

    def choose_investigation_path(self) -> None:
        """Option A — the doctor wants investigations before treating."""
        self._advance(VisitStatus.TEST_SELECTION)
        self._persist()

    def order_investigations(self, investigations: list[Investigation]) -> None:
        """Confirm the ordered tests. The patient leaves; the visit stays open.

        Investigations accumulate rather than replace. A second round ordered after
        reviewing results must not erase the first round — the results already recorded
        refer to those tests, and losing them leaves the record describing findings for
        investigations it claims were never ordered.
        """
        if not investigations:
            raise ConsultationError(
                "No investigations were given. Order at least one, or go to treatment "
                "instead."
            )
        self._advance(VisitStatus.WAITING_FOR_TESTS)
        self.visit.ordered_investigations = [
            *self.visit.ordered_investigations,
            *investigations,
        ]
        self._persist()

    def skip_investigations(self) -> None:
        """Go to treatment without ordering — Option B, or a change of mind."""
        self._advance(VisitStatus.TREATMENT_SELECTION)
        self._persist()

    def record_results(
        self, summary: str, resulted: Optional[list[str]] = None
    ) -> None:
        """The patient has returned with results. Typed text until Phase 6.

        `resulted` names the investigations that actually came back. Passing None marks
        every outstanding investigation as resulted, which is only correct when the
        doctor has confirmed they all arrived — the CLI asks about each one.

        Results arrive in batches — the chest film today, the culture on Thursday — so
        recording them a second time while already reviewing is a normal thing to do, not
        an error. The summary appends either way; only the move into RESULTS_REVIEW is
        skipped when the visit is already there.

        Note this adds no edge to the transition table: RESULTS_REVIEW still has exactly
        the two exits it always had. What changes is that arriving somewhere you already
        are is treated as a no-op rather than a refusal.
        """
        if self.visit.status is not VisitStatus.RESULTS_REVIEW:
            self._advance(VisitStatus.RESULTS_REVIEW)
        self.visit.results_summary = "\n".join(
            part for part in [self.visit.results_summary, summary] if part
        )
        for investigation in self.visit.ordered_investigations:
            if resulted is None or investigation.name in resulted:
                investigation.status = "resulted"
        self._persist()

    def revise_diagnosis(
        self, label: str, icd10_code: Optional[str] = None, reasoning: Optional[str] = None
    ) -> None:
        """Change the working diagnosis in the light of results.

        Deliberately not a state transition. Revising a diagnosis after seeing results
        is a different act from choosing one for the first time, and collapsing the two
        would let a visit re-enter diagnosis selection from anywhere.
        """
        if self.visit.status is not VisitStatus.RESULTS_REVIEW:
            raise ConsultationError(
                f"The working diagnosis can only be revised at RESULTS_REVIEW, "
                f"not at {self.visit.status.value}."
            )
        label = (label or "").strip()
        if not label:
            raise ConsultationError("A working diagnosis needs a label.")
        previous = self.visit.working_diagnosis
        self.visit.working_diagnosis = Diagnosis(
            label=label, icd10_code=icd10_code, reasoning=reasoning
        )
        if previous:
            self.add_note(f"Diagnosis revised after results (was: {previous.label}).")
        self._persist()

    def order_more_investigations(self) -> None:
        """Results raised a new question."""
        self._advance(VisitStatus.TEST_SELECTION)
        self._persist()

    def proceed_to_treatment(self) -> None:
        self._advance(VisitStatus.TREATMENT_SELECTION)
        self._persist()

    def prescribe(self, medications: list[PrescribedMedication]) -> None:
        if self.visit.status is not VisitStatus.TREATMENT_SELECTION:
            raise ConsultationError(
                f"Medications can only be recorded at TREATMENT_SELECTION, "
                f"not at {self.visit.status.value}."
            )
        self.visit.prescribed_medications = medications
        self._persist()

    def schedule_follow_up(self, plan: Optional[str] = None) -> None:
        self._advance(VisitStatus.FOLLOW_UP)
        if plan:
            self.add_note(f"Follow-up plan: {plan}")
        self._persist()

    def complete(self) -> None:
        self._advance(VisitStatus.COMPLETED)
        self._persist()

    def add_note(self, note: str) -> None:
        if not note:
            return
        self.visit.doctor_notes = "\n".join(
            part for part in [self.visit.doctor_notes, note] if part
        )
        self._persist()

    # --- internals ---------------------------------------------------------------

    def _advance(self, target: VisitStatus) -> None:
        require_transition(self.visit.status, target)
        self.visit.status = target

    def _persist(self) -> None:
        """Save after every decision, so a crash loses at most the current keystroke.

        Silently does nothing until the doctor has committed to a diagnosis — that is
        what keeps abandoned assessments out of the record.
        """
        if self._persisted:
            self._visits.save(self.visit)
