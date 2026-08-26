import { useEffect, useState } from 'react'
import { api } from '../api'

/**
 * A visit written up under the four headings.
 *
 * One component, used both inside the consultation and from the patient's profile, so the
 * note a doctor reads afterwards is the same document they saw while working — a second
 * renderer would eventually disagree with the first about something that matters.
 *
 * Nothing here is stored or editable. The note is rebuilt from the record each time it is
 * asked for, so it cannot go stale against the chart, and a note for a past visit shows
 * only what was known that day.
 */
export default function SoapNote({ visitId, onLoaded }) {
  const [note, setNote] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setNote(null)
    setError(null)
    api.soap(visitId)
      .then((n) => { setNote(n); onLoaded?.(n) })
      .catch(setError)
  }, [visitId])

  if (error) return <div className="note bad">{error.message}</div>
  if (!note) return <p className="empty">Building the note…</p>

  return (
    <div className="soap-note">
      <div className="row space">
        <div>
          <strong>{note.patient_name}</strong>{' '}
          <span className="muted small">{note.patient_summary}</span>
        </div>
        <span className="muted small">
          {note.visit_date} · <span className="mono">{note.visit_id}</span>
        </span>
      </div>

      <Section title="Subjective">
        <p>{note.subjective.chief_complaint || <em className="muted">Not recorded.</em>}</p>
        <List items={note.subjective.symptoms} />

        <h4>Allergies</h4>
        {/* Given weight rather than buried in a list: it is the line a prescriber has to
            see, and "None recorded" is not the same statement as "no allergies". */}
        {note.subjective.allergies?.length ? (
          <div className="note bad">
            <List items={note.subjective.allergies} bare />
          </div>
        ) : <p className="muted small">None recorded.</p>}

        <h4>Current medications</h4><List items={note.subjective.current_medications} />
        <h4>Past medical history</h4><List items={note.subjective.past_medical_history} />
        {note.subjective.social_history && (
          <><h4>Social</h4><p className="small">{note.subjective.social_history}</p></>
        )}
        {note.subjective.previous_diagnoses?.length > 0 && (
          <><h4>Previously diagnosed</h4><List items={note.subjective.previous_diagnoses} /></>
        )}
      </Section>

      <Section title="Objective">
        <h4>Vitals</h4><List items={note.objective.vitals} />
        {note.objective.physical_exam && (
          <><h4>Examination</h4><p className="small">{note.objective.physical_exam}</p></>
        )}
        {note.objective.investigation_results && (
          <><h4>Results</h4><pre className="block">{note.objective.investigation_results}</pre></>
        )}
        {note.objective.reports?.length > 0 && (
          <>
            <h4>Reports</h4>
            <ul className="small">
              {note.objective.reports.map((r, i) => (
                <li key={i}>
                  {r.label}
                  {!r.analysed && <span className="muted"> — uploaded but not analysed</span>}
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <Section title="Assessment">
        <p>
          <strong>{note.assessment.working_diagnosis}</strong>{' '}
          <span className="mono muted">{note.assessment.icd10_code || ''}</span>
        </p>
        {note.assessment.reasoning && <p className="small">{note.assessment.reasoning}</p>}

        {/* Under its own heading, never folded into the physician's assessment. The record
            keeps suggestion and decision apart; the note must not undo that at the end. */}
        {note.assessment.assistant_differential?.length > 0 && (
          <>
            <h4>
              Assistant-suggested <span className="tag tag-ai">AI</span>
              <span className="muted"> — not the clinician's assessment</span>
            </h4>
            <List items={note.assessment.assistant_differential} />
          </>
        )}
      </Section>

      <Section title="Plan">
        <h4>Investigations</h4><List items={note.plan.investigations} />
        <h4>Medications</h4><List items={note.plan.medications} />
        {note.plan.notes && <><h4>Notes</h4><pre className="block">{note.plan.notes}</pre></>}
      </Section>

      <p className="muted small">{note.disclaimer}</p>
    </div>
  )
}

const Section = ({ title, children }) => (
  <div className="card">
    <div className="card-head"><h2>{title}</h2></div>
    {children}
  </div>
)

const List = ({ items, bare = false }) =>
  items?.length
    ? <ul className={`small ${bare ? 'bare' : ''}`}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    : <p className="muted small">Not recorded.</p>
