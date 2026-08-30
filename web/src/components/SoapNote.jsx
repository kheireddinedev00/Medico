import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useDraft } from '../useDraft'

/**
 * A visit written up under the four headings.
 *
 * Two objects live behind this component and the difference matters:
 *
 * The **generated** note is a projection — assembled from the record on request, never
 * stored, so it cannot go stale against the chart it describes.
 *
 * The **saved** note is what a physician read, corrected and signed. It is stored, it has an
 * author and a timestamp, and it is what appears on the patient's profile. It cannot be
 * regenerated, because the edits are not derivable from anything.
 *
 * `editable` turns the draft into a form. Read-only elsewhere: the profile shows the signed
 * note, and a note is not something to revise from a list of visits.
 */
export default function SoapNote({ visitId, editable = false }) {
  const [generated, setGenerated] = useState(null)
  const [saved, setSaved] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [savedAt, setSavedAt] = useState(null)

  const [text, setText, clearText] = useDraft(`soap.${visitId}`, {
    subjective: '', objective: '', assessment: '', plan: '',
  })
  const [started, setStarted] = useState(false)

  useEffect(() => {
    setError(null)
    Promise.all([
      api.soap(visitId).catch(() => null),
      api.savedSoap(visitId).then((d) => d.note).catch(() => null),
    ]).then(([draft, signed]) => {
      setGenerated(draft)
      setSaved(signed)
      // A saved note wins as the starting text: it is what the physician last approved.
      // The generated draft is only the starting point when nobody has signed one yet.
      if (!started) {
        const source = signed ?? (draft && flatten(draft))
        if (source && !Object.values(text).some(Boolean)) {
          setText({
            subjective: source.subjective ?? '',
            objective: source.objective ?? '',
            assessment: source.assessment ?? '',
            plan: source.plan ?? '',
          })
        }
        setStarted(true)
      }
    })
  }, [visitId])

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.saveSoap(visitId, { ...text, generated })
      const signed = await api.savedSoap(visitId).then((d) => d.note)
      setSaved(signed)
      setSavedAt(new Date())
      clearText()
    } catch (e) { setError(e) } finally { setBusy(false) }
  }

  if (error && !generated) return <div className="note bad">{error.message}</div>
  if (!generated && !saved) return <p className="empty">Building the note…</p>

  // --- read-only: the signed note if there is one, otherwise the live projection ---
  if (!editable) {
    return saved
      ? <SignedNote note={saved} />
      : <Rendered note={generated} caveat="Not yet reviewed or saved by a clinician." />
  }

  return (
    <div className="soap-note">
      {error && <div className="note bad">{error.message}</div>}

      <div className={saved ? 'note ok' : 'note warn'}>
        {saved
          ? <>Saved to the patient record by <strong>{saved.saved_by ?? 'a clinician'}</strong>
              {' '}on {new Date(saved.saved_at).toLocaleString()}. Editing and saving replaces it.</>
          : <>This is a draft assembled from the record. <strong>It is not in the patient's
              profile until you save it.</strong> Read it, correct anything that is wrong,
              then save.</>}
      </div>

      <div className="form">
        {FIELDS.map(([key, label]) => (
          <label key={key}>{label}
            <AutoTextarea value={text[key]}
              onChange={(v) => setText({ ...text, [key]: v })} />
          </label>
        ))}
      </div>

      <div className="form-actions">
        <button className="primary" disabled={busy} onClick={save}>
          {busy ? 'Saving…' : saved ? 'Save changes' : 'Save to patient record'}
        </button>
        <button
          disabled={busy}
          onClick={() => generated && setText(flatten(generated))}
          title="Discard edits and rebuild from the record"
        >
          Reset to generated
        </button>
        {savedAt && <span className="pill ok">saved</span>}
      </div>

      {generated && (
        <details className="card">
          <summary>What the system generated</summary>
          {/* Kept visible so the physician can see what they changed, and so the record can
              show the proposal beside the signed version. */}
          <Rendered note={generated} />
        </details>
      )}
    </div>
  )
}

const FIELDS = [
  ['subjective', 'Subjective'],
  ['objective', 'Objective'],
  ['assessment', 'Assessment'],
  ['plan', 'Plan'],
]

/**
 * A textarea the height of its content.
 *
 * A clinical note is not a fixed-length thing — a subjective section can be two lines or
 * thirty. Scrolling inside a small box to read something you are being asked to check and
 * sign is exactly how a mistake gets missed, so the box grows and the whole note is on the
 * page at once.
 *
 * Measured with useLayoutEffect rather than an effect, so the resize happens in the same
 * frame as the text change and there is no visible jump while typing.
 */
function AutoTextarea({ value, onChange }) {
  const ref = useRef(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    // Collapse first: without it the box can only ever grow, never shrink back when text
    // is deleted.
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  return (
    <textarea
      ref={ref}
      className="grow"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/** The generated note as four blocks of text, ready to edit. */
function flatten(note) {
  const lines = (xs) => (xs?.length ? xs.map((x) => `- ${x}`).join('\n') : 'Not recorded.')
  const s = note.subjective ?? {}
  const o = note.objective ?? {}
  const a = note.assessment ?? {}
  const p = note.plan ?? {}

  return {
    subjective: [
      s.chief_complaint || 'Not recorded.',
      '', 'Symptoms:', lines(s.symptoms),
      '', 'Allergies:', lines(s.allergies),
      '', 'Current medications:', lines(s.current_medications),
      '', 'Past medical history:', lines(s.past_medical_history),
      s.social_history ? `\nSocial: ${s.social_history}` : '',
    ].join('\n').trim(),

    objective: [
      'Vitals:', lines(o.vitals),
      o.physical_exam ? `\nExamination:\n${o.physical_exam}` : '',
      o.investigation_results ? `\nResults:\n${o.investigation_results}` : '',
    ].join('\n').trim(),

    assessment: [
      a.working_diagnosis || 'Not recorded.',
      a.icd10_code ? `ICD-10: ${a.icd10_code}` : '',
      a.reasoning ? `\n${a.reasoning}` : '',
      // The assistant's differential is offered as text but flagged, so a physician who
      // keeps it in their note knows what they are keeping.
      a.assistant_differential?.length
        ? `\nAssistant-suggested (not the clinician's assessment):\n${lines(a.assistant_differential)}`
        : '',
    ].filter(Boolean).join('\n').trim(),

    plan: [
      'Investigations:', lines(p.investigations),
      '', 'Medications:', lines(p.medications),
      p.notes ? `\nNotes:\n${p.notes}` : '',
    ].join('\n').trim(),
  }
}

function SignedNote({ note }) {
  return (
    <div className="soap-note">
      <div className="note ok">
        Saved by <strong>{note.saved_by ?? 'a clinician'}</strong> on{' '}
        {new Date(note.saved_at).toLocaleString()}.
      </div>
      {FIELDS.map(([key, label]) => (
        <div key={key} className="card">
          <div className="card-head"><h2>{label}</h2></div>
          <pre className="block">{note[key] || 'Not recorded.'}</pre>
        </div>
      ))}
    </div>
  )
}

/** The generated projection, rendered structurally. */
function Rendered({ note, caveat }) {
  if (!note) return <p className="empty">Nothing to show.</p>

  const List = ({ items }) =>
    items?.length
      ? <ul className="small">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      : <p className="muted small">Not recorded.</p>

  return (
    <div className="soap-note">
      {caveat && <div className="note warn">{caveat}</div>}

      <div className="row space">
        <div>
          <strong>{note.patient_name}</strong>{' '}
          <span className="muted small">{note.patient_summary}</span>
        </div>
        <span className="muted small">
          {note.visit_date} · <span className="mono">{note.visit_id}</span>
        </span>
      </div>

      <div className="card">
        <div className="card-head"><h2>Subjective</h2></div>
        <p>{note.subjective.chief_complaint || <em className="muted">Not recorded.</em>}</p>
        <List items={note.subjective.symptoms} />
        <h4>Allergies</h4>
        {note.subjective.allergies?.length ? (
          <div className="note bad">
            <ul className="small bare">
              {note.subjective.allergies.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          </div>
        ) : <p className="muted small">None recorded.</p>}
        <h4>Current medications</h4><List items={note.subjective.current_medications} />
        <h4>Past medical history</h4><List items={note.subjective.past_medical_history} />
        {note.subjective.social_history && (
          <><h4>Social</h4><p className="small">{note.subjective.social_history}</p></>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h2>Objective</h2></div>
        <h4>Vitals</h4><List items={note.objective.vitals} />
        {note.objective.physical_exam && (
          <><h4>Examination</h4><p className="small">{note.objective.physical_exam}</p></>
        )}
        {note.objective.investigation_results && (
          <><h4>Results</h4><pre className="block">{note.objective.investigation_results}</pre></>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h2>Assessment</h2></div>
        <p>
          <strong>{note.assessment.working_diagnosis}</strong>{' '}
          <span className="mono muted">{note.assessment.icd10_code || ''}</span>
        </p>
        {note.assessment.reasoning && <p className="small">{note.assessment.reasoning}</p>}
        {note.assessment.assistant_differential?.length > 0 && (
          <>
            <h4>
              Assistant-suggested <span className="tag tag-ai">AI</span>
              <span className="muted"> — not the clinician's assessment</span>
            </h4>
            <List items={note.assessment.assistant_differential} />
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h2>Plan</h2></div>
        <h4>Investigations</h4><List items={note.plan.investigations} />
        <h4>Medications</h4><List items={note.plan.medications} />
        {note.plan.notes && <><h4>Notes</h4><pre className="block">{note.plan.notes}</pre></>}
      </div>

      <p className="muted small">{note.disclaimer}</p>
    </div>
  )
}
