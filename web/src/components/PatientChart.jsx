import { useEffect, useState } from 'react'
import { api } from '../api'

/**
 * A read-only chart, for looking something up without leaving what you are doing.
 *
 * Allergies come first and are given the most weight on the page, because this component
 * exists mainly to answer one question in the middle of a consultation: can I prescribe
 * this? An empty list says "None recorded" rather than "No allergies" — the two are
 * different clinical statements and the shorter one is the dangerous reading.
 *
 * Editing happens on the patient's own page. This is for reference.
 */
export default function PatientChart({ patientId }) {
  const [chart, setChart] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.patient(patientId).then(setChart).catch(setError)
  }, [patientId])

  if (error) return <div className="note bad">{error.message}</div>
  if (!chart) return <p className="empty">Loading chart…</p>

  const p = chart.patient
  const active = p.medications?.filter((m) => m.active) ?? []

  return (
    <div>
      <p className="muted small">
        <span className="mono">{p.id}</span> ·{' '}
        {chart.age != null ? `${chart.age} years` : 'age not recorded'} · {p.sex}
        {p.smoking_status !== 'unknown' && <> · {p.smoking_status} smoker
          {p.pack_years ? ` (${p.pack_years} pack-years)` : ''}</>}
      </p>

      <h3>Allergies</h3>
      {p.allergies?.length ? (
        <div className="note bad">
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {p.allergies.map((a) => (
              <li key={a.id}>
                <strong>{a.substance}</strong>
                {a.reaction && <> — {a.reaction}</>} ({a.severity})
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="empty small">None recorded — which is not the same as none.</p>
      )}

      <h3>Current medications</h3>
      {active.length ? (
        <ul className="small">
          {active.map((m) => (
            <li key={m.id}>
              <strong>{m.name}</strong> {[m.dose, m.frequency].filter(Boolean).join(' · ')}
              {m.indication && <span className="muted"> — for {m.indication}</span>}
            </li>
          ))}
        </ul>
      ) : <p className="empty small">None recorded.</p>}

      <h3>Chronic conditions</h3>
      {p.chronic_conditions?.length ? (
        <ul className="small">
          {p.chronic_conditions.map((c) => (
            <li key={c.id}>
              <strong>{c.name}</strong>{c.since && <span className="muted"> since {c.since}</span>}
              {c.notes && <div className="muted">{c.notes}</div>}
            </li>
          ))}
        </ul>
      ) : <p className="empty small">None recorded.</p>}

      <h3>Previous visits</h3>
      {chart.visits?.length ? (
        chart.visits.map((v) => (
          <div key={v.id} className="listrow small">
            <div className="row space">
              <span>{v.chief_complaint || <em className="muted">No complaint recorded.</em>}</span>
              <span className={v.status === 'COMPLETED' ? 'pill ok' : 'pill warn'}>
                {v.status.replace(/_/g, ' ')}
              </span>
            </div>
            {v.working_diagnosis_label && (
              <div><strong>{v.working_diagnosis_label}</strong>{' '}
                <span className="mono muted">{v.working_diagnosis_icd10 || ''}</span></div>
            )}
          </div>
        ))
      ) : <p className="empty small">None on record.</p>}

      {p.notes && (
        <>
          <h3>Notes</h3>
          <p className="small">{p.notes}</p>
        </>
      )}
    </div>
  )
}
