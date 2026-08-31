import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { IntakeForm, intakeTitle } from '../forms/PatientForms'
import SoapNote from '../components/SoapNote'
import Filter from '../components/Filter'

/**
 * The patient's chart.
 *
 * Everything the assistant is ever told about this person comes from here, which is why the
 * history sections are editable in place rather than being a read-only summary of something
 * entered elsewhere.
 */
export default function PatientProfilePage() {
  const { patientId } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()

  const [chart, setChart] = useState(null)
  const [reports, setReports] = useState(null)
  const [adding, setAdding] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [viewingNote, setViewingNote] = useState(null)
  const [deletingPatient, setDeletingPatient] = useState(false)
  const [editingSmoking, setEditingSmoking] = useState(false)
  const [error, setError] = useState(null)

  // Deleting a visit cuts against the append-only grain of the rest of the record, so it is
  // the doctor's or the admin's, and it asks first.
  const canDelete = user.role === 'doctor' || user.role === 'admin'
  // Deleting the *patient* is a larger act than deleting one visit — it takes the whole
  // history with it — so it is the administrator's alone.
  const canDeletePatient = user.role === 'admin'

  const refresh = useCallback(() => {
    api.patient(patientId).then(setChart).catch(setError)
    api.reports(patientId).then((d) => setReports(d.reports)).catch(() => setReports([]))
  }, [patientId])

  useEffect(() => { refresh() }, [refresh])

  const consult = async () => {
    try {
      const { visit } = await api.openConsultation(patientId)
      navigate(`/consultations/${visit.id}`)
    } catch (e) { setError(e) }
  }

  if (error) return <div className="page"><div className="note bad">{error.message}</div></div>
  if (!chart) return <p className="empty">Loading chart…</p>

  const p = chart.patient
  const initials = p.full_name.split(' ').map((w) => w[0]).slice(0, 2).join('')

  return (
    <div className="page">
      <div className="page-head">
        <div className="row">
          <div className="avatar">{initials}</div>
          <div>
            <h1>{p.full_name}</h1>
            <span className="muted small">
              <span className="mono">{p.id}</span> ·{' '}
              {chart.age != null ? `${chart.age} years` : 'age not recorded'} · {p.sex}
              {p.smoking_status !== 'unknown' && ` · ${p.smoking_status} smoker`}
            </span>
          </div>
        </div>
        <div className="row">
          {user.role === 'doctor' && (
            <button className="primary" onClick={consult}>Start consultation</button>
          )}
          {canDeletePatient && (
            <button className="danger" onClick={() => setDeletingPatient(true)}>
              Delete patient
            </button>
          )}
        </div>
      </div>

      <div className="grid2">
        <div>
          {/*
            Allergies come first, and say "None recorded" rather than "No allergies".
            An empty list and an unasked question are different clinical statements.
          */}
          <ListCard
            title="Allergies"
            items={p.allergies}
            empty="None recorded. This is not the same as 'no allergies' — the drug screen can only check what is here."
            onAdd={user.role !== 'patient' ? () => setAdding('allergy') : null}
            onRemove={(a) => api.removeAllergy(p.id, a.id).then(refresh)}
            render={(a) => (
              <>
                <strong>{a.substance}</strong>
                {a.reaction && <span className="muted"> — {a.reaction}</span>}{' '}
                <span className={`pill sev-${a.severity}`}>{a.severity}</span>
              </>
            )}
          />

          <ListCard
            title="Medications"
            items={p.medications}
            empty="None recorded."
            onAdd={user.role !== 'patient' ? () => setAdding('medication') : null}
            onRemove={(m) => api.removeMedication(p.id, m.id).then(refresh)}
            // Stopping before deleting: what someone used to take explains why a drug was
            // changed, and matters to the next prescriber.
            extra={(m) => m.active && user.role !== 'patient' && (
              <button onClick={() => api.stopMedication(p.id, m.id).then(refresh)}>Stop</button>
            )}
            render={(m) => (
              <>
                <strong>{m.name}</strong>{' '}
                <span className="muted">{[m.dose, m.frequency].filter(Boolean).join(' · ')}</span>
                {!m.active && <span className="pill"> stopped</span>}
              </>
            )}
          />

          <SmokingCard
            patient={p}
            editable={user.role !== 'patient'}
            editing={editingSmoking}
            onEdit={() => setEditingSmoking(true)}
            onCancel={() => setEditingSmoking(false)}
            onSaved={() => { setEditingSmoking(false); refresh() }}
            onError={setError}
          />

          <ListCard
            title="Chronic conditions"
            items={p.chronic_conditions}
            empty="None recorded."
            onAdd={user.role !== 'patient' ? () => setAdding('condition') : null}
            onRemove={(c) => api.removeCondition(p.id, c.id).then(refresh)}
            render={(c) => (
              <>
                <strong>{c.name}</strong>
                {c.since && <span className="muted"> since {c.since}</span>}
              </>
            )}
          />
        </div>

        <div>
          <div className="card">
            <div className="card-head"><h2>Visits</h2></div>
            {chart.visits.length === 0 && <p className="empty">No visits recorded.</p>}
            {chart.visits.map((v) => (
              <div key={v.id} className="listrow">
                <div className="row space">
                  <span className="mono small muted">{v.id}</span>
                  <span className={v.status === 'COMPLETED' ? 'pill ok' : 'pill warn'}>
                    {v.status.replace(/_/g, ' ')}
                  </span>
                </div>
                <div>{v.chief_complaint || <em className="muted">No complaint recorded.</em>}</div>
                <div className="small muted">
                  {v.doctor
                    ? <>Seen by <strong>{v.doctor}</strong></>
                    : <em>No doctor recorded</em>}
                </div>
                {v.working_diagnosis_label && (
                  <div className="small">
                    <strong>{v.working_diagnosis_label}</strong>{' '}
                    <span className="mono muted">{v.working_diagnosis_icd10 || ''}</span>
                  </div>
                )}
                <div className="row">
                  {/*
                    Available for a finished visit as much as an open one — arguably more.
                    The note is a projection of the record, not something produced during
                    the consultation, so reading last month's encounter is exactly what it
                    is for.
                  */}
                  {user.role === 'doctor' && (
                    <button onClick={() => setViewingNote(v)}>SOAP note</button>
                  )}
                  {user.role === 'doctor' && v.status !== 'COMPLETED' && (
                    <button onClick={() => navigate(`/consultations/${v.id}`)}>Resume</button>
                  )}
                  {canDelete && (
                    <button className="danger" onClick={() => setDeleting(v)}>Delete</button>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-head"><h2>Reports</h2></div>
            {(!reports || reports.length === 0) && <p className="empty">None uploaded.</p>}
            {reports?.map((r) => (
              <div key={r.id} className="listrow">
                <div className="row space">
                  <span>{r.label}</span>
                  {/* "Not analysed" is a clinical state, not a spinner. */}
                  <span className={r.analysed ? 'pill ok' : 'pill warn'}>
                    {r.analysed ? 'analysed' : 'not analysed'}
                  </span>
                </div>
                <div className="small muted">
                  {/* Which encounter it belongs to, and which test it answers. A result
                      floating free of both is filed but not usable. */}
                  {r.investigation && <>For: <strong>{r.investigation}</strong> · </>}
                  {r.visit_id
                    ? <>Visit <span className="mono">{r.visit_id}</span>{visitLabel(chart.visits, r.visit_id)}</>
                    : <em>Not linked to a visit — arrived outside a consultation.</em>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {adding && (
        <Modal title={intakeTitle(adding)} onClose={() => setAdding(null)}>
          <IntakeForm
            kind={adding}
            patientId={p.id}
            onCancel={() => setAdding(null)}
            onSaved={() => { setAdding(null); refresh() }}
          />
        </Modal>
      )}

      {viewingNote && (
        <Modal
          wide
          title={'SOAP note — ' + (viewingNote.chief_complaint || viewingNote.id)}
          onClose={() => setViewingNote(null)}
        >
          <SoapNote visitId={viewingNote.id} />
        </Modal>
      )}

      {deletingPatient && (
        <Modal title="Delete this patient?" onClose={() => setDeletingPatient(false)}>
          <p><strong>{p.full_name}</strong> <span className="mono muted">{p.id}</span></p>

          <div className="note bad">
            This removes the person and everything attached to them: every visit and
            everything decided in it, every allergy, medication and condition, every
            uploaded report, and every queue row. It cannot be undone from the interface.
          </div>

          <p className="muted small">
            The rest of this record is append-only — a mistaken entry is normally corrected
            rather than removed. This is for a duplicate or a record created in error, not
            for tidying up. The whole record is written to the audit log first.
          </p>

          <div className="form-actions">
            <button className="danger" onClick={async () => {
              try {
                await api.deletePatient(p.id)
                navigate('/patients')
              } catch (e) { setError(e); setDeletingPatient(false) }
            }}>Delete the whole record</button>
            <button onClick={() => setDeletingPatient(false)}>Keep it</button>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal title="Delete this visit?" onClose={() => setDeleting(null)}>
          <p>
            <strong>{deleting.chief_complaint || deleting.id}</strong>
            {deleting.working_diagnosis_label && <> — {deleting.working_diagnosis_label}</>}
          </p>
          <div className="note warn">
            The rest of this record is append-only: a mistaken entry is normally corrected by
            a new visit, not by removing the old one. This deletes the encounter and
            everything decided in it. Uploaded reports are kept and detached — a result that
            arrived belongs to the patient regardless.
          </div>
          <p className="muted small">The full visit is written to the audit log first.</p>
          <div className="form-actions">
            <button className="danger" onClick={async () => {
              try {
                await api.deleteVisit(deleting.id)
                setDeleting(null)
                refresh()
              } catch (e) { setError(e); setDeleting(null) }
            }}>Delete visit</button>
            <button onClick={() => setDeleting(null)}>Keep it</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

/** A short hint of which encounter a report belongs to. */
function visitLabel(visits, visitId) {
  const v = visits.find((x) => x.id === visitId)
  if (!v) return null
  const name = v.working_diagnosis_label || v.chief_complaint
  return name ? <> — {name}</> : null
}

/**
 * Smoking status, editable in place.
 *
 * Beside allergies and medications because it is the same kind of fact: background the
 * assistant reasons from, rather than something observed during an encounter. Pack-years
 * changes what a COPD differential is worth, so it is not cosmetic.
 *
 * "Not recorded" is offered as its own option and shown as its own state. Nobody having
 * asked is a different clinical statement from the patient never having smoked, and
 * collapsing the two is the same mistake as reading an empty allergy list as "no allergies".
 */
function SmokingCard({ patient, editable, editing, onEdit, onCancel, onSaved, onError }) {
  const [form, setForm] = useState({
    smoking_status: patient.smoking_status ?? 'unknown',
    pack_years: patient.pack_years ?? '',
    quit_year: patient.quit_year ?? '',
  })
  const [busy, setBusy] = useState(false)

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      await api.updatePatient(patient.id, {
        smoking_status: form.smoking_status,
        // Cleared for someone who never smoked: recording a quantity of something that
        // did not happen is worse than recording nothing.
        pack_years: form.smoking_status === 'never' ? null : (form.pack_years || null),
        quit_year: form.smoking_status === 'former' ? (form.quit_year || null) : null,
      })
      onSaved()
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>Smoking</h2>
        {editable && !editing && <button onClick={onEdit}>Edit</button>}
      </div>

      {!editing && (
        <>
          <div className="row">
            <span className={`pill ${SMOKING_TONE[patient.smoking_status] ?? ''}`}>
              {patient.smoking_status === 'unknown'
                ? 'not recorded'
                : `${patient.smoking_status} smoker`}
            </span>
            {patient.pack_years != null && (
              <span className="muted small">{patient.pack_years} pack-years</span>
            )}
            {patient.quit_year && <span className="muted small">quit {patient.quit_year}</span>}
          </div>

          {patient.smoking_status === 'unknown' && (
            <p className="empty small">
              Nobody has asked. Not the same as never — smoking history changes what a COPD
              differential is worth.
            </p>
          )}
        </>
      )}

      {editing && (
        <form className="form" onSubmit={save}>
          <label htmlFor="smoking">Status</label>
          <Filter
            id="smoking"
            value={form.smoking_status}
            onChange={(v) => setForm({ ...form, smoking_status: v })}
            options={[
              { value: 'never', label: 'Never smoked' },
              { value: 'former', label: 'Former smoker' },
              { value: 'current', label: 'Current smoker' },
              { value: 'unknown', label: 'Not recorded' },
            ]}
          />

          {form.smoking_status !== 'never' && form.smoking_status !== 'unknown' && (
            <div className="form-row">
              <label>Pack-years
                <input value={form.pack_years} inputMode="decimal" placeholder="20"
                  onChange={(e) => setForm({ ...form, pack_years: e.target.value })} />
              </label>
              {form.smoking_status === 'former' && (
                <label>Year quit
                  <input value={form.quit_year} inputMode="numeric" placeholder="2019"
                    onChange={(e) => setForm({ ...form, quit_year: e.target.value })} />
                </label>
              )}
            </div>
          )}

          <div className="form-actions">
            <button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
            <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  )
}

const SMOKING_TONE = { current: 'bad', former: 'warn', never: 'ok' }

function ListCard({ title, items, empty, render, onAdd, onRemove, extra }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {onAdd && <button onClick={onAdd}>Add</button>}
      </div>

      {(!items || items.length === 0) && <p className="empty small">{empty}</p>}

      {items?.map((item) => (
        <div key={item.id} className="row space listrow">
          <span>{render(item)}</span>
          <span className="row">
            {extra?.(item)}
            {onRemove && <button className="danger" onClick={() => onRemove(item)}>Remove</button>}
          </span>
        </div>
      ))}
    </div>
  )
}
