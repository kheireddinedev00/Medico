import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import Modal from '../components/Modal'
import PatientChart from '../components/PatientChart'
import * as S from '../consultation/Sections'
import { useDraft } from '../useDraft'

/**
 * One consultation.
 *
 * The steps follow the engine's workflow, and which transitions are legal comes from
 * `next-states` — answered out of the engine's own transition table. Nothing here keeps a
 * copy of the workflow, so nothing here can drift from it.
 *
 * Two navigation rules, and they pull in opposite directions on purpose:
 *
 * Finishing a step moves you to the next one, because that is what you were going to do
 * anyway and making someone click twice for an inevitability is just friction.
 *
 * But every step stays reachable at all times. A doctor mid-treatment who wants to reread
 * the findings, or re-run the differential now that results are in, must be able to — the
 * workflow governs what can be *recorded*, never what can be looked at.
 */

const STEPS = [
  { key: 'findings', label: 'Findings', done: (v) => !!v.chief_complaint },
  { key: 'differential', label: 'Differential', ai: true, done: (v) => v.differential?.length > 0 },
  { key: 'diagnosis', label: 'Diagnosis', done: (v) => !!v.working_diagnosis },
  { key: 'icd10', label: 'ICD-10', done: (v) => !!v.working_diagnosis?.icd10_code },
  { key: 'investigations', label: 'Investigations', done: (v) => v.ordered_investigations?.length > 0 },
  { key: 'results', label: 'Results & reports', done: (v) => !!v.results_summary },
  { key: 'treatment', label: 'Treatment', ai: true, done: (v) => v.prescribed_medications?.length > 0 },
  { key: 'soap', label: 'SOAP note' },
  { key: 'close', label: 'Close visit', done: (v) => v.status === 'COMPLETED' },
]

/** Where finishing one step naturally leads. */
const NEXT_AFTER = {
  findings: 'differential',
  differential: 'diagnosis',
  diagnosis: 'icd10',
  // icd10 opens the path dialog rather than moving to a step — see run().
  investigations: 'results',
  results: 'treatment',
  treatment: 'soap',
  soap: 'close',
}

export default function ConsultationPage() {
  const { visitId } = useParams()
  const navigate = useNavigate()

  const [visit, setVisit] = useState(null)
  const [patient, setPatient] = useState(null)
  const [persisted, setPersisted] = useState(false)
  // The transitions the engine says are legal, or null when that is not known — after a
  // failed lookup, or before the visit is part of the record.
  const [next, setNext] = useState(null)
  const [statesUnknown, setStatesUnknown] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [step, setStep] = useState(null)
  const [showChart, setShowChart] = useState(false)
  const [investigations, setInvestigations] = useState([])
  const [releasing, setReleasing] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [choosingPath, setChoosingPath] = useState(false)

  /*
   * Whether the doctor is reconsidering the diagnosis in the light of results.
   *
   * Intent, not record. The engine has no REASSESSING state and should not gain one — the
   * visit really is at RESULTS_REVIEW, and inventing a state to describe what someone is
   * currently thinking about would put a second, softer meaning into a table that is
   * supposed to say exactly where an encounter stands.
   *
   * So it lives in the browser, keyed to the visit so it survives navigating between steps,
   * and the badge below shows it alongside the real status rather than in place of it.
   */
  const [reassessing, setReassessing, clearReassessing] =
    useDraft(`reassessing.${visitId}`, false)

  const load = useCallback(async () => {
    try {
      const d = await api.resumeConsultation(visitId)
      setVisit(d.visit)
      setPatient(d.patient)
      setPersisted(d.persisted)
      setInvestigations(d.investigations ?? [])
      setStep((s) => s ?? (d.visit.status === 'WAITING_FOR_TESTS' ? 'results'
        : d.visit.chief_complaint ? 'differential' : 'findings'))
    } catch (e) { setError(e) }
  }, [visitId])

  useEffect(() => { load() }, [load])

  const refreshStates = useCallback(() => {
    // null means "we do not know", which is different from "nothing is allowed".
    if (!visit || !persisted) return setNext(null)

    api.nextStates(visit.id)
      .then((d) => { setNext(d.next); setStatesUnknown(false) })
      .catch(() => {
        // Do NOT empty the list. Emptying it disables every action in the workflow, with
        // no explanation, because a status lookup failed — the doctor is left clicking
        // dead buttons while the record is perfectly fine.
        setNext(null)
        setStatesUnknown(true)
      })
  }, [visit?.id, visit?.status, persisted])

  useEffect(() => { refreshStates() }, [refreshStates])

  // Reconsidering ends when the visit moves on — to treatment, or back to test selection.
  useEffect(() => {
    if (reassessing && visit && visit.status !== 'RESULTS_REVIEW') {
      setReassessing(false)
      clearReassessing()
    }
  }, [visit?.status, reassessing])

  /**
   * Run one call, absorb the result, and surface a clinical refusal as an explanation.
   *
   * `advance` names the step just completed; on success the view moves to whatever follows
   * it. Callers that are only fetching a suggestion leave it out — asking the assistant for
   * a differential has not finished anything.
   */
  const run = useCallback(async (label, fn, advance = null) => {
    setBusy(label)
    setError(null)
    try {
      const result = await fn()
      if (result?.visit) {
        setVisit(result.visit)
        if (result.persisted !== undefined) setPersisted(result.persisted)
        if (result.investigations) setInvestigations(result.investigations)
      }
      // Coding the diagnosis is the moment the two routes diverge, so the fork is asked
      // then — as a dialog, because it is one decision rather than a place to work.
      if (advance === 'icd10') setChoosingPath(true)
      else if (advance && NEXT_AFTER[advance]) setStep(NEXT_AFTER[advance])
      return result
    } catch (e) {
      setError(e)
      return null
    } finally {
      setBusy(null)
    }
  }, [])

  if (error && !visit) {
    return (
      <div className="centered">
        <div className="card" style={{ maxWidth: 460 }}>
          <div className="note bad">{error.message}</div>
          <button onClick={() => navigate('/patients')}>Back to patients</button>
        </div>
      </div>
    )
  }

  if (!visit) return <p className="empty centered">Opening consultation…</p>

  /*
   * Which route through the workflow this visit took.
   *
   * Derived rather than stored: a visit past ICD-10 with nothing ordered went straight to
   * treatment. Keeping a separate flag would be a second answer to a question the record
   * already answers, and the two would eventually disagree.
   */
  const pastIcd10 = !['INITIAL_ASSESSMENT', 'ICD10_SELECTION'].includes(visit.status)
  const ordered = (visit.ordered_investigations?.length ?? 0) > 0
  const treatmentOnly = pastIcd10 && !ordered && visit.status !== 'TEST_SELECTION'

  const shared = {
    visit, persisted, busy, run, setError, refreshStates, investigations, treatmentOnly,
    patientId: visit.patient_id,
    goTo: setStep,
    choosePath: () => setChoosingPath(true),
    reassessing,
    startReassessing: () => setReassessing(true),
    /*
     * Fail open, deliberately.
     *
     * This is a convenience: the engine is the authority on what is allowed, and it refuses
     * anything illegal with a message written for a clinician. So when the legal set is
     * unknown the button stays live — the worst case is a readable refusal, where the
     * alternative was a frozen workflow with nothing on screen to explain it.
     */
    can: (state) => next === null || next.includes(state),
    // Completing a visit ends the encounter, so the consultation screen has nothing left
    // to show. The doctor's next patient is in the queue, so that is where they land.
    onCompleted: () => navigate('/waiting-room'),
  }

  const Section = {
    findings: S.Findings, differential: S.Differential, diagnosis: S.Diagnosis,
    icd10: S.Icd10, investigations: S.Investigations,
    results: S.Results, treatment: S.Treatment, soap: S.Soap, close: S.CloseVisit,
  }[step] ?? S.Findings

  return (
    <div className="consultation">
      <aside className="side">
        <div>
          <h2>
            <button className="link chart-link" onClick={() => setShowChart(true)}>
              {patient?.full_name ?? 'Consultation'}
            </button>
          </h2>
          <span className="muted small mono">{visit.id}</span>
        </div>

        <div className="stack">
          {reassessing && visit.status === 'RESULTS_REVIEW' ? (
            <>
              <span className="pill warn">REASSESSING</span>
              {/* The record state stays on screen. A badge that replaced it would be the
                  interface telling a comfortable story about where the visit actually is. */}
              <span className="muted small">record state: results review</span>
            </>
          ) : (
            <span className="pill info">{visit.status.replace(/_/g, ' ')}</span>
          )}
          <span className={persisted ? 'pill ok' : 'pill warn'}>
            {persisted ? 'in the record' : 'not recorded yet'}
          </span>
        </div>

        <nav className="steps">
          {STEPS.map((s, i) => {
            // Greyed, not hidden, on the treatment-only path. Hiding them would leave the
            // doctor wondering where the investigations went; this says they were skipped.
            const skipped = treatmentOnly && ['investigations', 'results'].includes(s.key)
            return (
              /*
                Three columns, fixed: number, label, marker. The marker was previously
                inside a row that followed the label, so a tick sat wherever the text
                happened to end and nine of them zig-zagged down the panel. Now they
                stack in a straight line, and the number gives the workflow an order
                you can see rather than infer.
              */
              <button
                key={s.key}
                className={`${step === s.key ? 'on' : ''} ${skipped ? 'skipped' : ''}`}
                onClick={() => setStep(s.key)}
                title={skipped ? 'Skipped — this visit went straight to treatment' : undefined}
              >
                <span className="step-icon" aria-hidden="true">{i + 1}</span>

                <span className="step-label">
                  {s.label}
                  {s.ai && <span className="tag-ai">AI</span>}
                </span>

                {skipped
                  ? <span className="marker" title="Skipped">—</span>
                  : s.done?.(visit)
                    ? <span className="done" title="Recorded">✓</span>
                    : <span className="marker" aria-hidden="true" />}
              </button>
            )
          })}
        </nav>

        {!persisted && (
          <p className="muted small">
            Nothing is written until a diagnosis is chosen.
          </p>
        )}

        {/* Said out loud rather than silently disabling things. */}
        {statesUnknown && (
          <p className="muted small">
            Could not read the workflow state — the assistant may be down. Steps stay
            available; anything not allowed will say so when you try it.
          </p>
        )}

        <div className="exits">
          {/*
            The queue, not browser history. `navigate(-1)` returned wherever the doctor
            happened to come from — a chart, a search, the patients list — which is rarely
            where they are going next. Stepping out of a consultation means going back to
            the room, and the patient is still in it.
          */}
          <button className="back" onClick={() => navigate('/waiting-room')}>← Back</button>
          <p className="muted small">Keeps the patient in the waiting room.</p>

          <button disabled={busy} onClick={() => setReleasing(true)}>Release patient</button>
          <button className="danger" disabled={busy} onClick={() => setResetting(true)}>
            Reset visit
          </button>
        </div>
      </aside>

      <div className="panel">
        {error && (
          <div className={error instanceof ApiError && error.isRefusal ? 'note warn' : 'note bad'}>
            {error instanceof ApiError && error.isRefusal && (
              <strong>Not allowed at this step. </strong>
            )}
            {error.message}
          </div>
        )}
        <Section {...shared} />
      </div>

      {choosingPath && (
        <Modal title="What next?" onClose={() => setChoosingPath(false)}>
          {/*
            Repeated inside the dialog on purpose. The panel's copy of this is *behind* the
            backdrop, so a refusal raised by one of these buttons was invisible — the button
            looked simply broken, which is the worst way for a workflow rule to be enforced.
          */}
          {error && (
            <div className={error instanceof ApiError && error.isRefusal ? 'note warn' : 'note bad'}>
              {error instanceof ApiError && error.isRefusal && (
                <strong>Not allowed at this step. </strong>
              )}
              {error.message}
            </div>
          )}
          <S.ChoosePath
            {...shared}
            onDone={(next) => { setChoosingPath(false); if (next) setStep(next) }}
          />
        </Modal>
      )}

      {resetting && (
        <Modal title="Reset this consultation?" onClose={() => setResetting(false)}>
          <div className="note bad">
            Everything recorded in this visit is erased — findings, diagnosis, ordered tests,
            results and prescriptions. The consultation starts again from the beginning.
          </div>
          <p className="muted small">
            Uploaded reports are kept and detached; a result that arrived belongs to the
            patient regardless. The whole visit is written to the audit log first, so what
            was erased can still be read there.
          </p>
          <div className="form-actions">
            <button className="danger" disabled={busy} onClick={async () => {
              const r = await run('reset', () => api.resetConsultation(visit.id))
              if (r) {
                setResetting(false)
                setStep('findings')
                // The row is gone; the emptied visit is a draft again, under the same URL.
                navigate(`/consultations/${r.visit.id}`, { replace: true })
              }
            }}>Erase and start again</button>
            <button onClick={() => setResetting(false)}>Keep what is recorded</button>
          </div>
        </Modal>
      )}

      {releasing && (
        <Modal title="Release the patient?" onClose={() => setReleasing(false)}>
          <p>
            {patient?.full_name} leaves the waiting room. The visit stays open
            {visit.status !== 'COMPLETED' && <> at <strong>{visit.status.replace(/_/g, ' ').toLowerCase()}</strong></>}
            {' '}and can be resumed from <strong>In progress</strong>.
          </p>
          <p className="muted small">
            When they come back, the nurse adds them to the queue again and takes fresh
            vitals — readings belong to a visit to the clinic, not to the person.
          </p>
          <div className="form-actions">
            <button className="primary" onClick={async () => {
              await api.leaveConsultation(visit.id).catch(() => {})
              navigate(persisted ? '/in-progress' : '/waiting-room')
            }}>Release</button>
            <button onClick={() => setReleasing(false)}>Stay in the consultation</button>
          </div>
        </Modal>
      )}

      {showChart && (
        <Modal wide title={patient?.full_name ?? 'Patient chart'} onClose={() => setShowChart(false)}>
          <PatientChart patientId={visit.patient_id} />
        </Modal>
      )}
    </div>
  )
}
