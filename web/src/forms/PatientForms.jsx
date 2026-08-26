import { useState } from 'react'
import { api } from '../api'

/**
 * Registering a patient, and taking their history.
 *
 * The history is the important half. Allergies, medications and conditions enter the system
 * only here — the assistant cannot write to the record, and a consultation captures the
 * encounter rather than the background. An allergy not entered here is one the drug safety
 * screen will never get the chance to withhold a drug for.
 */

export function NewPatientForm({ onCreated, onCancel }) {
  const [values, setValues] = useState({ sex: 'unknown' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const set = (k) => (e) => setValues({ ...values, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { patient } = await api.createPatient({
        ...values,
        date_of_birth: values.date_of_birth || null,
        age_years: values.age_years ? Number(values.age_years) : null,
      })
      onCreated(patient)
    } catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}

      <label>Full name
        <input value={values.full_name ?? ''} onChange={set('full_name')} autoFocus />
      </label>

      <div className="form-row">
        <label>Sex
          <select value={values.sex} onChange={set('sex')}>
            {['unknown', 'male', 'female', 'other'].map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label>Date of birth
          <input type="date" value={values.date_of_birth ?? ''} onChange={set('date_of_birth')} />
        </label>
      </div>

      {/* For someone who knows their age but not their date of birth — common, and better
          than inventing a date that will later be read as fact. */}
      <label>Age in years, if the date of birth is unknown
        <input value={values.age_years ?? ''} onChange={set('age_years')} placeholder="e.g. 54" />
      </label>

      <label>Notes
        <input value={values.notes ?? ''} onChange={set('notes')} placeholder="Occupation, living situation…" />
      </label>

      <p className="muted small">
        History — allergies, medications, conditions — is taken next and matters more than
        anything on this form.
      </p>

      <div className="form-actions">
        <button className="primary" disabled={busy || !values.full_name}>
          {busy ? 'Registering…' : 'Register patient'}
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

const FORMS = {
  allergy: {
    title: 'Add an allergy',
    fields: [
      { key: 'substance', label: 'Substance', required: true, placeholder: 'Penicillin' },
      { key: 'reaction', label: 'Reaction', placeholder: 'urticaria and facial swelling' },
      { key: 'severity', label: 'Severity', type: 'select', options: ['unknown', 'mild', 'moderate', 'severe'] },
    ],
    // Free text on purpose. The engine maps the substance onto a drug class at screening
    // time, so normalising it here would only lose what the patient actually said.
    submit: (id, v) => api.addAllergy(id, v),
  },
  medication: {
    title: 'Add a medication',
    fields: [
      { key: 'name', label: 'Name', required: true, placeholder: 'Salbutamol' },
      { key: 'dose', label: 'Dose', placeholder: '100 mcg' },
      { key: 'frequency', label: 'Frequency', placeholder: 'inhaled as needed' },
      { key: 'indication', label: 'For', placeholder: 'breathlessness' },
      { key: 'started', label: 'Started', placeholder: '2019 · 6 months ago' },
    ],
    submit: (id, v) => api.addMedication(id, v),
  },
  condition: {
    title: 'Add a chronic condition',
    fields: [
      { key: 'name', label: 'Condition', required: true, placeholder: 'COPD' },
      { key: 'since', label: 'Since', placeholder: '2016' },
      { key: 'notes', label: 'Notes', placeholder: 'GOLD group B' },
    ],
    submit: (id, v) => api.addCondition(id, v),
  },
}

export function IntakeForm({ kind, patientId, onSaved, onCancel }) {
  const spec = FORMS[kind]
  const [values, setValues] = useState(
    Object.fromEntries(spec.fields.filter((f) => f.type === 'select').map((f) => [f.key, f.options[0]]))
  )
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const complete = spec.fields.filter((f) => f.required).every((f) => values[f.key]?.trim())

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await spec.submit(patientId, values)
      onSaved()
    } catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}

      {spec.fields.map((f, i) => (
        <label key={f.key}>{f.label}
          {f.type === 'select' ? (
            <select value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
              {f.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : (
            <input autoFocus={i === 0} value={values[f.key] ?? ''} placeholder={f.placeholder}
              onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          )}
        </label>
      ))}

      <div className="form-actions">
        <button className="primary" disabled={busy || !complete}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

export const intakeTitle = (kind) => FORMS[kind].title
