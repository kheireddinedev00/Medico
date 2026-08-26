import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import Modal from '../components/Modal'
import PatientChart from '../components/PatientChart'
import * as S from '../consultation/Sections'

/**
 * One consultation.
 *
 * The steps in the sidebar follow the engine's workflow, and which transitions are legal
 * comes from `next-states` — answered out of the engine's own transition table. Nothing
 * here keeps a copy of the workflow, so nothing here can drift from it.
 *
 * Every step stays *readable* at any point; what the visit's state governs is the buttons
 * inside. Locking the navigation would hide the record from the person responsible for it,
 * and a doctor waiting on bloods still has every right to read the treatment options.
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

export default function ConsultationPage() {
  const { visitId } = useParams()
  const navigate = useNavigate()

  const [visit, setVisit] = useState(null)
  const [patient, setPatient] = useState(null)
  const [persisted, setPersisted] = useState(false)
  const [next, setNext] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [step, setStep] = useState(null)
  const [showChart, setShowChart] = useState(false)
  const [investigations, setInvestigations] = useState([])
  const [releasing, setReleasing] = useState(false)

  const load = useCallback(async () => {
    try {
      const d = await api.resumeConsultation(visitId)
      setVisit(d.visit)
      setPatient(d.patient)
      setPersisted(d.persisted)
      setInvestigations(d.investigations ?? [])
      // Land where the work actually is: a visit waiting on tests opens at Results.
      setStep((s) => s ?? (d.visit.status === 'WAITING_FOR_TESTS' ? 'results'
        : d.visit.chief_complaint ? 'differential' : 'findings'))
    } catch (e) { setError(e) }
  }, [visitId])

  useEffect(() => { load() }, [load])

  const refreshStates = useCallback(() => {
    if (!visit || !persisted) return setNext([])
    api.nextStates(visit.id).then((d) => setNext(d.next)).catch(() => setNext([]))
  }, [visit?.id, visit?.status, persisted])

  useEffect(() => { refreshStates() }, [refreshStates])

  /** Run one call, absorb the result, and surface a clinical refusal as an explanation. */
  const run = useCallback(async (label, fn) => {
    setBusy(label)
    setError(null)
    try {
      const result = await fn()
      if (result?.visit) {
        setVisit(result.visit)
        if (result.persisted !== undefined) setPersisted(result.persisted)
        if (result.investigations) setInvestigations(result.investigations)
      }
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

  const shared = {
    visit, persisted, busy, run, setError, refreshStates, investigations,
    patientId: visit.patient_id,
    can: (state) => next.includes(state),
  }

  const Section = {
    findings: S.Findings, differential: S.Differential, diagnosis: S.Diagnosis,
    icd10: S.Icd10, investigations: S.Investigations, results: S.Results,
    treatment: S.Treatment, soap: S.Soap, close: S.CloseVisit,
  }[step] ?? S.Findings

  return (
    <div className="consultation">
      <aside className="side">
        <div>
          {/* The chart is one click away at every step. A doctor mid-consultation needs to
              check an allergy or a past visit without abandoning what they are doing. */}
          <h2>
            <button className="link chart-link" onClick={() => setShowChart(true)}>
              {patient?.full_name ?? 'Consultation'}
            </button>
          </h2>
          <span className="muted small mono">{visit.id}</span>
        </div>

        <div className="stack">
          <span className="pill info">{visit.status.replace(/_/g, ' ')}</span>
          {/* Not a loading state: this says whether the encounter exists in the record. */}
          <span className={persisted ? 'pill ok' : 'pill warn'}>
            {persisted ? 'in the record' : 'not recorded yet'}
          </span>
        </div>

        <nav className="steps">
          {STEPS.map((s) => (
            <button key={s.key} className={step === s.key ? 'on' : ''} onClick={() => setStep(s.key)}>
              <span>{s.label}</span>
              <span className="row" style={{ gap: 4 }}>
                {s.ai && <span className="tag tag-ai">AI</span>}
                {s.done?.(visit) && <span className="done">✓</span>}
              </span>
            </button>
          ))}
        </nav>

        {!persisted && (
          <p className="muted small">
            Nothing is written until a diagnosis is chosen. Leave now and this consultation
            leaves no trace.
          </p>
        )}

        {/*
          Two ways out, and they mean different things.

          Back steps away without ending anything: the patient is still in the clinic, still
          in the queue, and the consultation is resumable from there. It is what a doctor
          does to check something.

          Release says the patient can go. The visit stays open — one waiting on tests is
          still waiting — but the waiting-room row closes and the next attendance will be
          measured afresh.
        */}
        <div className="exits">
          <button className="back" onClick={() => navigate(-1)}>
            ← Back
          </button>
          <p className="muted small">Keeps the patient in the waiting room.</p>

          <button disabled={busy} onClick={() => setReleasing(true)}>
            Release patient
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
              navigate(persisted ? '/in-progress' : '/patients')
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
