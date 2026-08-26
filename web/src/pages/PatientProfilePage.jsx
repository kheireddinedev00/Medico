import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { IntakeForm, intakeTitle } from '../forms/PatientForms'
import SoapNote from '../components/SoapNote'

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
  const [error, setError] = useState(null)

  // Deleting a visit cuts against the append-only grain of the rest of the record, so it is
  // the doctor's or the admin's, and it asks first.
  const canDelete = user.role === 'doctor' || user.role === 'admin'

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
        {user.role === 'doctor' && (
          <button className="primary" onClick={consult}>Start consultation</button>
        )}
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
