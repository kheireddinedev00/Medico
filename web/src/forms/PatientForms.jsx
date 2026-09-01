import { useState } from 'react'
import { api } from '../api'
import Filter from '../components/Filter'

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
        {/* A label wrapping its control, like every other field on this form. As a bare
            div beside a label it missed the 5px the others get and sat a few pixels
            high of the date beside it. */}
        <label htmlFor="sex">Sex
          <Filter
            id="sex"
            value={values.sex}
            onChange={(v) => setValues({ ...values, sex: v })}
            options={['unknown', 'male', 'female']
              .map((s) => ({ value: s, label: labelFor(s) }))}
          />
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
        /*
          A choice renders as the application's own menu, not the operating system's.
          The label sits outside rather than wrapping, because `Filter` is a button and a
          list — wrapping it in a `<label>` would make clicking the label toggle the menu
          rather than focus it. `htmlFor` does the same job without that side effect.
        */
        f.type === 'select' ? (
          <div key={f.key}>
            <label htmlFor={`intake-${f.key}`}>{f.label}</label>
            <Filter
              id={`intake-${f.key}`}
              value={values[f.key]}
              onChange={(v) => setValues({ ...values, [f.key]: v })}
              options={f.options.map((o) => ({ value: o, label: labelFor(o) }))}
            />
          </div>
        ) : (
          <label key={f.key}>{f.label}
            <input autoFocus={i === 0} value={values[f.key] ?? ''} placeholder={f.placeholder}
              onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          </label>
        )
      ))}

      <div className="form-actions">
        <button className="primary" disabled={busy || !complete}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

/**
 * How an option is written in a menu.
 *
 * `unknown` is the one that matters. Rendered as "Not recorded" it says nobody answered,
 * which is the honest reading; left as the raw value it looks like a severity the clinician
 * picked. An unasked question and a recorded answer are different clinical statements —
 * the same distinction the allergy list itself makes.
 */
function labelFor(value) {
  if (value === 'unknown') return 'Not recorded'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export const intakeTitle = (kind) => FORMS[kind].title
