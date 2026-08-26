import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { NewPatientForm } from '../forms/PatientForms'

const VITALS = [
  ['temperature_c', 'Temp °C', '36.8'],
  ['heart_rate', 'Heart rate', '78'],
  ['respiratory_rate', 'Resp. rate', '16'],
  ['blood_pressure', 'Blood pressure', '120/80'],
  ['spo2', 'SpO₂ %', '98'],
  ['weight_kg', 'Weight kg', '70'],
]

/**
 * The queue.
 *
 * The nurse owns it: who arrived, what they are here about, and their vitals. The doctor
 * reads it and starts consultations from it, but adds nobody — two people managing one list
 * is how a queue stops matching the room.
 *
 * Ordered by arrival, with no priority column, because nothing computes one yet. The triage
 * agent is a later phase; an interface showing an invented priority would be worse than one
 * admitting it sorts by who came first.
 */
export default function WaitingRoomPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [entries, setEntries] = useState([])
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)

  const isNurse = user.role === 'nurse' || user.role === 'admin'

  const refresh = useCallback(async () => {
    try { setEntries((await api.queue()).entries) } catch (e) { setError(e) }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const consult = async (entry) => {
    try {
      // The nurse said what this arrival is about. Resume that visit rather than opening a
      // second consultation for the same episode.
      const visitId = entry.active_visit_id
        ?? (await api.openConsultation(entry.patient.id)).visit.id

      // Record the visit on the queue row, including a brand-new one. Without this a
      // doctor who steps out and comes back would be handed a second empty consultation
      // for the same patient, and the first one's findings would be stranded.
      await api.markSeen(entry.id, visitId).catch(() => {})

      navigate(`/consultations/${visitId}`)
    } catch (e) { setError(e) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Waiting room</h1>
        {isNurse && (
          <div className="row">
            <button onClick={() => setModal({ kind: 'search' })}>Add existing patient</button>
            <button className="primary" onClick={() => setModal({ kind: 'register' })}>
              New patient
            </button>
          </div>
        )}
      </div>

      {error && <div className="note bad">{error.message}</div>}

      {entries.length === 0 && (
        <div className="card">
          <p className="empty">
            Nobody is waiting.{!isNurse && ' The nurse adds patients to the queue.'}
          </p>
        </div>
      )}

      {entries.map((e) => (
        <div key={e.id} className="card">
          <div className="card-head">
            <div className="row">
              <div className="avatar">
                {e.patient.full_name.split(' ').map((w) => w[0]).slice(0, 2).join('')}
              </div>
              <div>
                {/* The name is a link. Whoever is looking at the queue may need the chart
                    before deciding anything. */}
                <h2><Link to={`/patients/${e.patient.id}`}>{e.patient.full_name}</Link></h2>
                <span className="muted small">
                  {e.patient.age ?? '?'} years · <span className="mono">{e.patient.id}</span>
                </span>
              </div>
            </div>
            <div className="stack">
              <span className="pill info">waiting {e.waiting_label}</span>
              {/* Being seen is not the same as gone. The row stays until the doctor
                  releases them, so the queue matches who is actually in the clinic. */}
              {e.status === 'in_consultation' && (
                <span className="pill ok">with the doctor</span>
              )}
              <span className={e.vitals_recorded ? 'pill ok' : 'pill warn'}>
                {e.vitals_recorded ? 'vitals recorded' : 'vitals pending'}
              </span>
            </div>
          </div>

          {/* What they are here about. A new problem, or a visit they are coming back to. */}
          <VisitChoice entry={e} isNurse={isNurse} onChanged={refresh} onError={setError} />

          <div className="vitals">
            {VITALS.map(([k, label]) => (
              <span key={k}>
                <span className="muted">{label}:</span>{' '}
                {e.vitals[k] ?? <em className="muted">not measured</em>}
              </span>
            ))}
          </div>

          <div className="row wrap">
            {isNurse && (
              <button className={e.vitals_recorded ? '' : 'primary'}
                onClick={() => setModal({ kind: 'vitals', entry: e })}>
                {e.vitals_recorded ? 'Update vitals' : 'Record vitals'}
              </button>
            )}
            <button onClick={() => navigate(`/patients/${e.patient.id}`)}>Profile</button>
            {user.role === 'doctor' && (
              <button className="primary" onClick={() => consult(e)}>
                {e.status === 'in_consultation'
                  ? 'Back to consultation'
                  : e.visit_id ? 'Resume consultation' : 'Start consultation'}
              </button>
            )}
            {isNurse && e.status !== 'in_consultation' && (
              <button className="danger" onClick={() => api.removeFromQueue(e.id).then(refresh)}>
                Left without being seen
              </button>
            )}
          </div>
        </div>
      ))}

      {modal?.kind === 'register' && (
        <Modal title="Register a patient" onClose={() => setModal(null)}>
          <NewPatientForm
            onCancel={() => setModal(null)}
            onCreated={async (p) => {
              setModal(null)
              await api.arrive(p.id).catch(setError)
              // Straight to the chart: registering someone is the preamble to recording
              // their history, which is what the assistant will actually read.
              navigate(`/patients/${p.id}`)
            }}
          />
        </Modal>
      )}

      {modal?.kind === 'search' && (
        <Modal title="Add a patient to the queue" onClose={() => setModal(null)}>
          <PatientSearch
            onCancel={() => setModal(null)}
            onPicked={async (patient, visitId) => {
              setModal(null)
              try { await api.arrive(patient.id, visitId); refresh() } catch (e) { setError(e) }
            }}
          />
        </Modal>
      )}

      {modal?.kind === 'vitals' && (
        <Modal title={`Vitals — ${modal.entry.patient.full_name}`} onClose={() => setModal(null)}>
          <VitalsForm
            entry={modal.entry}
            onCancel={() => setModal(null)}
            // Refresh before closing so the row shows the readings rather than staying
            // on "not measured" until someone reloads.
            onSaved={async () => { await refresh(); setModal(null) }}
            onError={setError}
          />
        </Modal>
      )}
    </div>
  )
}

/**
 * Search by name, then say what they are here about.
 *
 * Asking rather than guessing: a patient back for their results belongs on the visit that
 * ordered them. Opening a second consultation for the same episode leaves a chart with two
 * half-finished visits and no way to tell which one matters.
 */
function PatientSearch({ onPicked, onCancel }) {
  const [term, setTerm] = useState('')
  const [results, setResults] = useState([])
  const [picked, setPicked] = useState(null)
  const [visits, setVisits] = useState([])
  const [choice, setChoice] = useState('')

  useEffect(() => {
    const t = setTimeout(() => {
      api.patients(term).then((d) => setResults(d.patients.data ?? [])).catch(() => {})
    }, 200)
    return () => clearTimeout(t)
  }, [term])

  const pick = async (p) => {
    setPicked(p)
    setChoice('')
    try { setVisits((await api.resumableVisits(p.id)).visits) } catch { setVisits([]) }
  }

  if (picked) {
    return (
      <div className="form">
        <div className="row space">
          <strong>{picked.full_name}</strong>
          <button onClick={() => setPicked(null)}>Change</button>
        </div>

        <label>What are they here about?
          <select value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">New problem — start a new visit</option>
            {visits.map((v) => (
              <option key={v.id} value={v.id}>
                Resume: {v.working_diagnosis_label || v.chief_complaint || v.id}
                {' '}({v.status.replace(/_/g, ' ').toLowerCase()})
              </option>
            ))}
          </select>
        </label>

        {visits.length === 0 && (
          <p className="muted small">No open visits — this will be a new one.</p>
        )}

        <div className="form-actions">
          <button className="primary" onClick={() => onPicked(picked, choice || null)}>
            Add to queue
          </button>
          <button onClick={onCancel}>Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className="form">
      <label>Search by name or id
        <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
          placeholder="Start typing…" />
      </label>

      {results.length === 0 && <p className="empty small">No matches.</p>}

      {results.map((p) => (
        <div key={p.id} className="row space listrow">
          <span>
            <strong>{p.full_name}</strong>{' '}
            <span className="mono muted small">{p.id}</span>
          </span>
          <button onClick={() => pick(p)}>Select</button>
        </div>
      ))}
    </div>
  )
}

/** The visit this arrival is about, changeable while they are still waiting. */
function VisitChoice({ entry, isNurse, onChanged, onError }) {
  const [visits, setVisits] = useState(null)
  const [editing, setEditing] = useState(false)

  const open = async () => {
    setEditing(true)
    try { setVisits((await api.resumableVisits(entry.patient.id)).visits) } catch { setVisits([]) }
  }

  const choose = async (visitId) => {
    try {
      await api.setQueueVisit(entry.id, visitId || null)
      setEditing(false)
      onChanged()
    } catch (e) { onError(e) }
  }

  if (editing) {
    return (
      <div className="row" style={{ margin: '8px 0' }}>
        <select defaultValue={entry.visit_id ?? ''} onChange={(e) => choose(e.target.value)}>
          <option value="">New problem — new visit</option>
          {(visits ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              Resume: {v.working_diagnosis_label || v.chief_complaint || v.id}
              {' '}({v.status.replace(/_/g, ' ').toLowerCase()})
            </option>
          ))}
        </select>
        <button onClick={() => setEditing(false)}>Cancel</button>
      </div>
    )
  }

  return (
    <div className="row" style={{ margin: '8px 0' }}>
      {entry.visit ? (
        <span className="pill warn">
          Resuming: {entry.visit.working_diagnosis || entry.visit.chief_complaint || entry.visit.id}
          {' · '}{entry.visit.status.replace(/_/g, ' ').toLowerCase()}
        </span>
      ) : (
        <span className="pill">New visit</span>
      )}
      {isNurse && <button className="link" onClick={open}>change</button>}
    </div>
  )
}

function VitalsForm({ entry, onSaved, onCancel, onError }) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(VITALS.map(([k]) => [k, entry.vitals[k] ?? '']))
  )
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    // Blank travels as null, never zero. A recorded 0% saturation is a very different
    // statement from an unrecorded one.
    const payload = Object.fromEntries(
      Object.entries(values).map(([k, v]) => [k, v === '' ? null : v])
    )
    try { await api.recordVitals(entry.id, payload); await onSaved() }
    catch (err) { onError(err) } finally { setBusy(false) }
  }

  return (
    <form className="form" onSubmit={submit}>
      <div className="vitals-grid">
        {VITALS.map(([k, label, placeholder]) => (
          <label key={k}>{label}
            <input value={values[k] ?? ''} placeholder={placeholder}
              onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          </label>
        ))}
      </div>
      <p className="muted small">
        Leave blank what was not measured. Blank is recorded as "not measured", which is not
        the same as normal.
      </p>
      <div className="form-actions">
        <button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save vitals'}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}
