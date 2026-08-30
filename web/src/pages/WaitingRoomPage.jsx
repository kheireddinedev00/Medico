import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { NewPatientForm } from '../forms/PatientForms'
import { useDraft } from '../useDraft'

const VITALS = [
  ['temperature_c', 'Temp °C', '36.8'],
  ['heart_rate', 'Heart rate', '78'],
  ['respiratory_rate', 'Resp. rate', '16'],
  ['blood_pressure', 'Blood pressure', '120/80'],
  ['spo2', 'SpO₂ %', '98'],
  ['weight_kg', 'Weight kg', '70'],
]

const PRIORITIES = ['CRITICAL', 'URGENT', 'STANDARD', 'LOW']

/**
 * How a priority is painted. Severity only — never a shape or a colour on its own,
 * because a queue read by someone colour-blind has to work too, and every badge carries
 * its word.
 */
const PRIORITY_CLASS = {
  CRITICAL: 'bad',
  URGENT: 'warn',
  STANDARD: 'info',
  LOW: 'ok',
}

/**
 * The queue.
 *
 * The nurse owns it: who arrived, what they are here about, and their vitals. The doctor
 * reads it and starts consultations from it, but adds nobody — two people managing one list
 * is how a queue stops matching the room.
 *
 * Ordered by triage priority, then by arrival within a priority. The order arrives from the
 * API already sorted; this page does not re-sort, because the ordering rule lives in the
 * engine and a second copy of it here would eventually disagree.
 *
 * Three things this screen refuses to do:
 *
 * - Show an unscored patient as a low priority. "Not scored" is its own badge. Nobody has
 *   measured them, which is a reason to look at them sooner rather than later.
 * - Show a priority without its reason. Every badge expands into what decided it: the NEWS2
 *   parameters, the red flags, what was missing.
 * - Hide a disagreement. When a nurse overrides the agent, both answers stay on screen.
 */
/**
 * Where the sort preference is kept.
 *
 * Per browser, not per clinic. Ordering by arrival is a way of finding someone rather than
 * a decision about who is treated first, so one person switching their own view must not
 * reorder everybody else's screen.
 */
const ORDER_KEY = 'waiting-room-order'

export default function WaitingRoomPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [entries, setEntries] = useState([])
  const [counts, setCounts] = useState(null)
  const [order, setOrder] = useState(() => localStorage.getItem(ORDER_KEY) ?? 'priority')
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)
  const [doctors, setDoctors] = useState([])

  const isNurse = user.role === 'nurse' || user.role === 'admin'

  const refresh = useCallback(async () => {
    try {
      const data = await api.queue('active', order)
      setEntries(data.entries)
      setCounts(data.counts)
    } catch (e) { setError(e) }
  }, [order])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    api.doctors().then((d) => setDoctors(d.doctors)).catch(() => setDoctors([]))
  }, [])

  const changeOrder = (next) => {
    localStorage.setItem(ORDER_KEY, next)
    setOrder(next)   // refresh follows, because `order` is a dependency of it
  }

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
        <div className="row">
          <OrderToggle order={order} onChange={changeOrder} />
          {isNurse && (
            <>
              <button onClick={() => setModal({ kind: 'search' })}>Add existing patient</button>
              <button className="primary" onClick={() => setModal({ kind: 'register' })}>
                New patient
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="note bad">{error.message}</div>}

      {/*
        Sorting by arrival is a legitimate way to find someone, and a bad way to decide
        who is next. When it is on and somebody urgent is waiting, the list says so —
        the priorities are all still on the rows, but they are no longer at the top where
        a glance would catch them.
      */}
      {order === 'arrival' && counts && (counts.CRITICAL > 0 || counts.URGENT > 0) && (
        <div className="note warn">
          Sorted by arrival time. {counts.CRITICAL > 0 && <><strong>{counts.CRITICAL} CRITICAL</strong>{counts.URGENT > 0 && ' and '}</>}
          {counts.URGENT > 0 && <><strong>{counts.URGENT} URGENT</strong></>}
          {' '}waiting, not shown first.{' '}
          <button className="link" onClick={() => changeOrder('priority')}>
            Sort by priority
          </button>
        </div>
      )}

      {entries.length === 0 && (
        <div className="card">
          <p className="empty">
            {isNurse
              ? 'Nobody is waiting.'
              : 'No patients assigned to you. The nurses assign patients from the shared queue.'}
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
              <PriorityBadge triage={e.triage} />
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

          {/* Who this patient is waiting for. The nurse's call, changeable while they wait. */}
          <AssignedDoctor
            entry={e} doctors={doctors} isNurse={isNurse}
            onChanged={refresh} onError={setError}
          />

          {/* What they are here about. A new problem, or a visit they are coming back to. */}
          <VisitChoice entry={e} isNurse={isNurse} onChanged={refresh} onError={setError} />

          {/* The complaint drives half the priority, so it is on the row rather than
              hidden inside the vitals form. */}
          {e.chief_complaint && <p className="complaint">“{e.chief_complaint}”</p>}

          <TriagePanel entry={e} onChanged={refresh} onError={setError} />

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
            <button onClick={() => setModal({ kind: 'priority', entry: e })}>
              {e.triage.override ? 'Change priority' : 'Override priority'}
            </button>
            {/* Only offered when there is something to re-score. Re-running on unchanged
                observations returns the same answer, so an always-on button would just
                teach people it does nothing. */}
            {isNurse && e.vitals_recorded && !e.triage.scored && (
              <button onClick={() => api.retriage(e.id).then(refresh).catch(setError)}>
                Retry scoring
              </button>
            )}
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
            doctors={doctors}
            onCancel={() => setModal(null)}
            onPicked={async (patient, visitId, doctorId) => {
              setModal(null)
              try {
                await api.arrive(patient.id, visitId, doctorId)
                refresh()
              } catch (e) { setError(e) }
            }}
          />
        </Modal>
      )}

      {modal?.kind === 'priority' && (
        <Modal title={`Priority — ${modal.entry.patient.full_name}`} onClose={() => setModal(null)}>
          <PriorityForm
            entry={modal.entry}
            onCancel={() => setModal(null)}
            onSaved={async () => { await refresh(); setModal(null) }}
            onError={setError}
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
 * Which doctor the patient is waiting for.
 *
 * Shown on every row, including when it is nobody. "Unassigned" is a real state rather than
 * a blank: the nurses can see it and fix it, and no doctor is handed a patient nobody gave
 * them.
 *
 * A patient already with a doctor cannot be reassigned — moving them mid-consultation would
 * leave the visit attached to one clinician and the queue pointing at another.
 */
function AssignedDoctor({ entry, doctors, isNurse, onChanged, onError }) {
  const [editing, setEditing] = useState(false)

  const choose = async (value) => {
    try {
      await api.assignDoctor(entry.id, value === '' ? null : Number(value))
      setEditing(false)
      onChanged()
    } catch (e) { onError(e) }
  }

  const locked = entry.status === 'in_consultation'

  if (editing) {
    return (
      <div className="row" style={{ margin: '8px 0' }}>
        <select defaultValue={entry.doctor?.id ?? ''} onChange={(ev) => choose(ev.target.value)}>
          <option value="">Unassigned</option>
          {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <button onClick={() => setEditing(false)}>Cancel</button>
      </div>
    )
  }

  return (
    <div className="row" style={{ margin: '8px 0' }}>
      {entry.doctor
        ? <span className="pill info">Doctor: {entry.doctor.name}</span>
        : <span className="pill warn">No doctor assigned</span>}
      {isNurse && !locked && (
        <button className="link" onClick={() => setEditing(true)}>
          {entry.doctor ? 'reassign' : 'assign'}
        </button>
      )}
      {isNurse && locked && (
        <span className="muted small">in consultation — cannot reassign</span>
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
function PatientSearch({ onPicked, onCancel, doctors }) {
  const [term, setTerm] = useState('')
  const [results, setResults] = useState([])
  const [picked, setPicked] = useState(null)
  const [visits, setVisits] = useState([])
  const [choice, setChoice] = useState('')
  const [doctorId, setDoctorId] = useState('')

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

        {/*
          Assigning here rather than in a second step. Left unassigned the patient sits in
          nobody's queue: the nurses can see them, and no doctor has been told about them.
          That is a legitimate answer while the nurse decides, so it stays available — but
          it is not the default anyone should reach by not noticing the field.
        */}
        <label>Which doctor?
          <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
            <option value="">Decide later — nobody sees them yet</option>
            {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>

        <div className="form-actions">
          <button
            className="primary"
            onClick={() => onPicked(picked, choice || null, doctorId ? Number(doctorId) : null)}
          >
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

/**
 * Sort by priority, or by arrival.
 *
 * Two buttons rather than a checkbox, because "sorting: off" does not say what the order
 * then is, and a queue whose ordering rule is unstated is one people guess at.
 *
 * Deliberately not restricted to nurses. The doctor scanning for a particular patient has
 * the same need, and this changes nobody's screen but their own.
 */
function OrderToggle({ order, onChange }) {
  return (
    <div className="row" role="group" aria-label="Queue order">
      <button className={order === 'priority' ? 'primary' : ''}
        aria-pressed={order === 'priority'}
        title="Sickest first, then longest wait"
        onClick={() => onChange('priority')}>
        By priority
      </button>
      <button className={order === 'arrival' ? 'primary' : ''}
        aria-pressed={order === 'arrival'}
        title="Purely who arrived first. Priorities are still shown, just not sorted on."
        onClick={() => onChange('arrival')}>
        By arrival
      </button>
    </div>
  )
}

/**
 * The priority, as one badge.
 *
 * "Not scored" is a state of its own and never falls back to LOW. An arrival nobody has
 * measured is not a patient known to be well, and a queue that renders the two the same
 * way is a queue that will eventually leave someone sitting there.
 */
function PriorityBadge({ triage }) {
  if (!triage?.scored) {
    return <span className="pill" title="No observations recorded yet">not scored</span>
  }

  const priority = triage.priority
  return (
    <span className={`pill ${PRIORITY_CLASS[priority] ?? ''}`}
      title={triage.recommended_action ?? ''}>
      {priority}
      {triage.override && ' (nurse)'}
    </span>
  )
}

/**
 * Why this patient has this priority.
 *
 * Collapsed to one line, because a queue is scanned rather than read. Expanded it shows
 * everything that decided the priority — which is the whole claim of the project: an AI
 * decision a clinician can check rather than one they have to trust.
 */
function TriagePanel({ entry, onChanged, onError }) {
  const [open, setOpen] = useState(false)
  const t = entry.triage

  if (!t.scored) {
    return (
      <p className="muted small">
        {entry.vitals_recorded
          ? 'Observations recorded but not scored — the triage engine was unreachable.'
          : 'Not scored yet. Record observations and a complaint to triage this patient.'}
      </p>
    )
  }

  return (
    <div className="triage">
      <div className="row wrap">
        <span className="small">{t.reason}</span>
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? 'hide detail' : 'why?'}
        </button>
      </div>

      <div className="row wrap small muted">
        {t.news2 && (
          <span title={`${t.news2.scored_count} of ${t.news2.expected_count} parameters recorded`}>
            NEWS2 {t.news2.aggregate}
            {!t.news2.scored_count || t.news2.scored_count < t.news2.expected_count
              ? ` (${t.news2.scored_count}/${t.news2.expected_count})` : ''}
          </span>
        )}
        {t.red_flags.length > 0 && <span>· {t.red_flags.length} red flag(s)</span>}
        {/* The status is not a priority. It says whether the agent could assess this
            patient at all, which matters even when a priority came out anyway. */}
        {t.status !== 'OK' && <span className="pill warn">{t.status.replace(/_/g, ' ')}</span>}
        {t.degraded && (
          <span className="pill" title="The model was unreachable. The priority comes from the deterministic rules, which is a complete result.">
            rules only
          </span>
        )}
        {t.ai_escalated && (
          <span className="pill warn" title="The AI raised this above what the rules alone decided.">
            AI escalated
          </span>
        )}
      </div>

      {open && (
        <div className="triage-detail">
          {t.override && (
            <div className="note">
              <strong>Nurse override:</strong> {t.override.priority} — {t.override.reason}
              <div className="muted small">
                The agent said {t.suggested_priority}. Both are kept.{' '}
                <button className="link"
                  onClick={() => api.clearPriority(entry.id).then(onChanged).catch(onError)}>
                  revert to the agent's
                </button>
              </div>
            </div>
          )}

          <Detail title="Why" items={t.reasons} />
          <Detail title="Concerning findings" items={t.concerning_findings} />
          <Detail title="Red flags" items={t.red_flags.map((f) => `${f.label} (${f.floor})`)} />
          <Detail title="Missing information" items={t.missing_information} />
          <Detail title="Data quality" items={t.data_quality_issues} />

          {t.ai_summary && <p className="small"><strong>AI summary:</strong> {t.ai_summary}</p>}

          <p className="muted small">
            {t.recommended_action}
            {' · '}Decision support only — a clinician decides. Scored by rule set{' '}
            <span className="mono">{t.ruleset_version}</span>.
          </p>
        </div>
      )}
    </div>
  )
}

function Detail({ title, items }) {
  if (!items || items.length === 0) return null
  return (
    <div className="triage-section">
      <strong className="small">{title}</strong>
      <ul className="small">{items.map((it, i) => <li key={i}>{it}</li>)}</ul>
    </div>
  )
}

/**
 * The clinician disagreeing with the agent.
 *
 * A reason is required — the API rejects an empty one, and this form does too, so the
 * requirement is visible rather than arriving as a validation error. An override with no
 * reason is indistinguishable from a misclick when someone asks in three weeks why this
 * patient was moved up.
 */
function PriorityForm({ entry, onSaved, onCancel, onError }) {
  const t = entry.triage
  const [priority, setPriority] = useState(t.override?.priority ?? t.suggested_priority ?? 'STANDARD')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try { await api.setPriority(entry.id, priority, reason.trim()); await onSaved() }
    catch (err) { onError(err) } finally { setBusy(false) }
  }

  return (
    <form className="form" onSubmit={submit}>
      {t.scored ? (
        <p className="small">
          The agent assigned <strong>{t.suggested_priority}</strong>
          {t.reason && <> — {t.reason}</>}
        </p>
      ) : (
        <p className="small muted">This patient has not been scored.</p>
      )}

      <label>Priority
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>

      <label>Why are you overriding it?
        <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="What you can see that the observations do not show." />
      </label>

      <p className="muted small">
        Your decision is recorded beside the agent's, not over it. Both stay on the record
        so the two can be compared.
      </p>

      <div className="form-actions">
        <button className="primary" disabled={busy || reason.trim().length < 3}>
          {busy ? 'Saving…' : 'Set priority'}
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

/**
 * Triage assessment: what the patient says, and what was measured.
 *
 * Saving this scores the patient, so the form asks for everything the scale needs rather
 * than the numbers alone. Two additions are not optional decorations:
 *
 * - **The complaint.** Half the priority comes from it. Crushing chest pain with a
 *   flawless set of observations is the case the red-flag layer exists for, and without
 *   this field the agent would sort that patient as well.
 * - **Consciousness and oxygen.** Both are NEWS2 parameters. Leave them out and the scale
 *   can only ever score five of seven, and everyone comes back as incomplete.
 *
 * Every control has an explicit "not assessed" option and it is the default. None of them
 * fall back to the normal value: the whole system is built on the difference between a
 * measurement that was normal and one nobody took.
 */
function VitalsForm({ entry, onSaved, onCancel, onError }) {
  const obs = entry.triage_observations ?? {}
  // Held in the draft store: a nurse who closes the form to check something should not
  // come back to an empty one and have to measure again.
  const [values, setValues, clearValues] = useDraft(
    `queue-vitals.${entry.id}`,
    Object.fromEntries(VITALS.map(([k]) => [k, entry.vitals[k] ?? ''])),
  )
  const [complaint, setComplaint] = useState(entry.chief_complaint ?? '')
  const [notes, setNotes] = useState(entry.triage_notes ?? '')
  const [consciousness, setConsciousness] = useState(obs.consciousness ?? '')
  const [onOxygen, setOnOxygen] = useState(triState(obs.on_oxygen))
  const [pregnant, setPregnant] = useState(triState(obs.is_pregnant))
  const [targetRange, setTargetRange] = useState(Boolean(obs.hypercapnic_target_range))
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    // Blank travels as null, never zero. A recorded 0% saturation is a very different
    // statement from an unrecorded one.
    const payload = {
      ...Object.fromEntries(
        Object.entries(values).map(([k, v]) => [k, v === '' ? null : v])
      ),
      chief_complaint: complaint.trim() || null,
      triage_notes: notes.trim() || null,
      consciousness: consciousness || null,
      on_oxygen: fromTriState(onOxygen),
      is_pregnant: fromTriState(pregnant),
      hypercapnic_target_range: targetRange,
    }
    try { await api.recordVitals(entry.id, payload); clearValues(); await onSaved() }
    catch (err) { onError(err) } finally { setBusy(false) }
  }

  return (
    <form className="form" onSubmit={submit}>
      <label>What are they here about?
        <textarea rows={2} value={complaint} autoFocus
          onChange={(e) => setComplaint(e.target.value)}
          placeholder="In their words — “crushing chest pain, started an hour ago”" />
      </label>

      <div className="vitals-grid">
        {VITALS.map(([k, label, placeholder]) => (
          <label key={k}>{label}
            <input value={values[k] ?? ''} placeholder={placeholder}
              onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          </label>
        ))}

        <label>Consciousness
          <select value={consciousness} onChange={(e) => setConsciousness(e.target.value)}>
            <option value="">not assessed</option>
            <option value="alert">alert</option>
            {/* New confusion scores the same as a reduced conscious level — that is why
                NEWS2 added the C to AVPU. */}
            <option value="confusion">new confusion</option>
            <option value="voice">responds to voice</option>
            <option value="pain">responds to pain</option>
            <option value="unresponsive">unresponsive</option>
          </select>
        </label>

        <label>On oxygen?
          <select value={onOxygen} onChange={(e) => setOnOxygen(e.target.value)}>
            <option value="">not recorded</option>
            <option value="no">no — breathing air</option>
            <option value="yes">yes</option>
          </select>
        </label>
      </div>

      <label>Notes
        <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything else that changes how urgent this is." />
      </label>

      <details>
        <summary className="small muted">Things that change which thresholds apply</summary>

        <label>Pregnant?
          <select value={pregnant} onChange={(e) => setPregnant(e.target.value)}>
            <option value="">not asked</option>
            <option value="no">no</option>
            <option value="yes">yes</option>
          </select>
        </label>
        <p className="muted small">
          The scale is not validated in pregnancy. Marking this sends the patient straight
          to a clinician instead of scoring them on thresholds that do not apply.
        </p>

        <label className="row">
          <input type="checkbox" checked={targetRange}
            onChange={(e) => setTargetRange(e.target.checked)} />
          <span>Documented oxygen target range of 88–92% (NEWS2 Scale 2)</span>
        </label>
        <p className="muted small">
          Only tick this from a prescribed target in the notes — never because the patient
          has COPD. Without a target range, a saturation of 89% is significant hypoxia; with
          one it is normal for them, and getting that backwards hides a sick patient.
        </p>
      </details>

      <p className="muted small">
        Leave blank what was not measured. Blank is recorded as "not measured", which is not
        the same as normal. Saving scores this patient and re-orders the queue.
      </p>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {busy ? 'Saving and scoring…' : 'Save and triage'}
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

/** null/undefined -> '' so "not recorded" is a real, selectable option rather than a gap. */
function triState(value) {
  if (value === true) return 'yes'
  if (value === false) return 'no'
  return ''
}

function fromTriState(value) {
  if (value === 'yes') return true
  if (value === 'no') return false
  return null
}
