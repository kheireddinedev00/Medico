import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useDraft } from '../useDraft'
import SoapNote from '../components/SoapNote'

/**
 * The steps of a consultation.
 *
 * One rule runs through all of them: **what the assistant suggested and what the physician
 * decided never look alike.** Assistant output carries the purple AI tag and sits in its own
 * block; a decision only happens when a human clicks to make one.
 */

const VITALS = [
  ['temperature_c', 'Temp °C'], ['heart_rate', 'Heart rate'], ['respiratory_rate', 'Resp. rate'],
  ['blood_pressure', 'Blood pressure'], ['spo2', 'SpO₂ %'], ['weight_kg', 'Weight kg'],
]

/** Drop the nulls, so accepting triage readings never overwrites a value with "not measured". */
const prune = (vitals) =>
  Object.fromEntries(Object.entries(vitals ?? {}).filter(([, v]) => v != null))

const AiHeading = ({ children, action }) => (
  <div className="page-head">
    <h2>{children} <span className="tag tag-ai">AI</span></h2>
    {action}
  </div>
)

// --- findings -------------------------------------------------------------------

export function Findings({ visit, run, busy, patientId }) {
  // Kept in the draft store, so stepping away to check the chart and coming back does not
  // discard what has been typed.
  const [f, setF, clearF] = useDraft(`findings.${visit.id}`, {
    chief_complaint: visit.chief_complaint ?? '',
    symptoms: (visit.symptoms ?? []).join(', '),
    physical_exam: visit.physical_exam ?? '',
  })
  const [vitals, setVitals, clearVitals] = useDraft(`vitals.${visit.id}`, visit.vitals ?? {})
  const [triage, setTriage] = useState(null)

  /**
   * What the nurse measured on *this* attendance.
   *
   * Fetched per visit, because vitals belong to a visit to the clinic rather than to the
   * person: a patient returning for results is measured again, and last week's readings
   * must never appear as though they were today's.
   *
   * Offered rather than applied. The doctor presses to accept them, and the record then
   * says what the doctor observed — not what an interface copied in behind them.
   */
  useEffect(() => {
    api.triageVitals(patientId, visit.id)
      .then((d) => { if (d.recorded) setTriage(d) })
      .catch(() => {})
  }, [patientId, visit.id])

  const hasOwn = Object.values(vitals).some((v) => v != null && v !== '')

  const save = () => run('findings', async () => {
    const result = await api.findings(visit.id, {
      chief_complaint: f.chief_complaint,
      symptoms: f.symptoms.split(',').map((s) => s.trim()).filter(Boolean),
      physical_exam: f.physical_exam || null,
      vitals,
    })
    // Cleared only once it is safely in the visit. A draft that outlives what it drafted is
    // how two versions of the same note start disagreeing.
    clearF()
    clearVitals()
    return result
  }, 'findings')

  return (
    <section>
      <div className="page-head"><h2>Findings</h2></div>

      <div className="card">
        <div className="form">
          <label>Chief complaint
            <input value={f.chief_complaint}
              onChange={(e) => setF({ ...f, chief_complaint: e.target.value })} />
          </label>
          <label>Symptoms (comma separated)
            <input value={f.symptoms} onChange={(e) => setF({ ...f, symptoms: e.target.value })}
              placeholder="cough, fever, pleuritic pain" />
          </label>
          <label>Examination
            <textarea rows={3} value={f.physical_exam}
              onChange={(e) => setF({ ...f, physical_exam: e.target.value })} />
          </label>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Vitals</h2>
          {!triage && <span className="pill warn">nurse has not recorded any</span>}
        </div>

        {triage && (
          <div className="note ok">
            <div className="row space">
              <span>
                <strong>Recorded at triage</strong>{' '}
                <span className="muted small">
                  {new Date(triage.taken_at).toLocaleString()}
                </span>
              </span>
              <button onClick={() => setVitals({ ...vitals, ...prune(triage.vitals) })}>
                {hasOwn ? 'Replace with these' : 'Use these'}
              </button>
            </div>
            <div className="vitals">
              {VITALS.map(([k, label]) => (
                <span key={k}>
                  <span className="muted">{label}:</span>{' '}
                  {triage.vitals[k] ?? <em className="muted">not measured</em>}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="vitals-grid">
          {VITALS.map(([k, label]) => (
            <label key={k}>{label}
              <input value={vitals[k] ?? ''} placeholder="—"
                onChange={(e) => setVitals({ ...vitals, [k]: e.target.value === '' ? null : e.target.value })} />
            </label>
          ))}
        </div>
        <p className="muted small">
          What the doctor records here belongs to this visit. Leave blank what was not
          measured — blank means "not measured", not normal.
        </p>
      </div>

      <button className="primary" disabled={busy} onClick={save}>
        {busy === 'findings' ? 'Saving…' : 'Record findings'}
      </button>
    </section>
  )
}

// --- the assistant ----------------------------------------------------------------

export function Differential({ visit, run, busy, reassessing }) {
  /*
   * Held in the draft store rather than component state.
   *
   * Stepping over to re-read the findings and coming back used to discard the differential,
   * so the only way to see it again was to ask the assistant again — a slow, rate-limited
   * call for an answer that had already been given. The suggestion is not a decision and is
   * not part of the record until one is made, so keeping it in the browser is right; it is
   * cleared when the visit is reset, along with every other draft.
   */
  const [assessment, setAssessment] = useDraft(`assessment.${visit.id}`, null)

  // Re-assessing after results: the diagnosis already exists, so picking one revises it.
  const reconsidering = visit.status === 'RESULTS_REVIEW' && !!visit.working_diagnosis

  const ask = () => run('assess', async () => {
    const r = await api.assess(visit.id)
    setAssessment(r.assessment)
    return r
  })

  return (
    <section>
      <AiHeading action={
        <button className="primary" disabled={busy} onClick={ask}>
          {busy === 'assess' ? 'Thinking…' : assessment ? 'Re-assess' : 'Ask the assistant'}
        </button>
      }>Differential</AiHeading>

      {reassessing && (
        <div className="note warn">
          <strong>Reassessing after results.</strong> The assistant is working from the
          chart including everything recorded in this visit. Choosing a diagnosis here
          revises the existing one rather than replacing the encounter.
        </div>
      )}

      <p className="muted small">
        Suggestions only. Choosing one is the physician's decision, and the point at which
        this consultation enters the record.
        {visit.results_summary && ' Results already recorded are included when re-assessing.'}
      </p>

      {!assessment && <div className="card"><p className="empty">Nothing asked yet.</p></div>}

      {assessment?.differential?.map((d, i) => (
        <div key={i} className="card">
          <div className="card-head">
            <h2>{d.label}</h2>
            {/* Words, never a percentage — the engine rejects numeric likelihoods. */}
            <span className="pill">{d.likelihood}</span>
          </div>
          <p className="small">{d.reasoning}</p>
          {/*
            Choosing after results is a *revision*, not a first choice, and the engine
            treats them as different acts — selecting again would try to move the visit back
            to ICD10_SELECTION, which the workflow refuses. Same button, right call.
          */}
          <button className="primary" disabled={busy} onClick={() => run('dx', () =>
            reconsidering
              ? api.reviseDiagnosis(visit.id, { label: d.label, reasoning: d.reasoning })
              : api.selectDiagnosis(visit.id, { label: d.label, reasoning: d.reasoning }),
            'diagnosis',
          )}>
            {reconsidering ? 'Revise to this diagnosis' : 'Choose as working diagnosis'}
          </button>
        </div>
      ))}

      {assessment?.questions?.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Worth asking</h2></div>
          <ul className="small">{assessment.questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
        </div>
      )}

      {assessment?.sources?.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Evidence</h2></div>
          {/* Citations come from what was retrieved, not from the model. */}
          <ul className="small">
            {assessment.sources.map((s, i) => (
              <li key={i}>{s.citation || s.source}{s.page ? ` — p.${s.page}` : ''}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

// --- decisions ----------------------------------------------------------------------

export function Diagnosis({ visit, run, busy }) {
  const [d, setD, clearD] = useDraft(`diagnosis.${visit.id}`, { label: '', reasoning: '' })
  const { label, reasoning } = d
  const setLabel = (v) => setD({ ...d, label: v })
  const setReasoning = (v) => setD({ ...d, reasoning: v })

  const dx = visit.working_diagnosis
  // Revising is a different act from choosing, and the engine only permits it once results
  // are under review. The form says so rather than offering a button that will be refused.
  const revising = dx && visit.status === 'RESULTS_REVIEW'

  return (
    <section>
      <div className="page-head"><h2>Working diagnosis</h2></div>

      {dx && (
        <div className="card">
          <div className="card-head">
            <h2>{dx.label}</h2>
            <span className="pill ok">{dx.icd10_code || 'no code'}</span>
          </div>
          {dx.reasoning && <p className="small muted">{dx.reasoning}</p>}
        </div>
      )}

      {(!dx || revising) && (
        <div className="card">
          <div className="form">
            <label>{revising ? 'Revised diagnosis' : 'Diagnosis'}
              <input value={label} onChange={(e) => setLabel(e.target.value)}
                placeholder="The physician's decision" />
            </label>
            <label>Reasoning
              <textarea rows={2} value={reasoning} onChange={(e) => setReasoning(e.target.value)} />
            </label>
            <button className="primary" disabled={busy || !label} onClick={() => run('dx', async () => {
              const result = revising
                ? await api.reviseDiagnosis(visit.id, { label, reasoning: reasoning || null })
                : await api.selectDiagnosis(visit.id, { label, reasoning: reasoning || null })
              clearD()
              return result
            }, 'diagnosis')}>{revising ? 'Revise diagnosis' : 'Record diagnosis'}</button>
          </div>
        </div>
      )}

      {dx && !revising && (
        <p className="muted small">
          The diagnosis can only be revised once results are under review.
        </p>
      )}
    </section>
  )
}

export function Icd10({ visit, run, busy, choosePath }) {
  const [codes, setCodes] = useState(null)

  return (
    <section>
      <AiHeading action={
        <button disabled={busy || !visit.working_diagnosis} onClick={() => run('codes', async () => {
          setCodes(await api.suggestCodes(visit.id)); return null
        })}>{busy === 'codes' ? 'Thinking…' : 'Suggest codes'}</button>
      }>ICD-10</AiHeading>

      {!visit.working_diagnosis && (
        <div className="card"><p className="empty">Choose a working diagnosis first.</p></div>
      )}

      {visit.status === 'ICD10_SELECTION' && visit.working_diagnosis && (
        <div className="card">
          <div className="row space">
            <span className="small">Ready to decide how this visit proceeds?</span>
            <button className="primary" onClick={choosePath}>Choose path</button>
          </div>
        </div>
      )}

      {visit.status === 'RESULTS_REVIEW' && visit.working_diagnosis && (
        <div className="note warn">
          The diagnosis was revised after results. Re-code it if the code no longer matches —
          an old code left on a new diagnosis is worse than none.
        </div>
      )}

      {visit.working_diagnosis?.icd10_code && (
        <div className="note ok">
          Coded as <strong>{visit.working_diagnosis.icd10_code}</strong>
        </div>
      )}

      {codes && (
        <div className="card">
          <div className="card-head"><h2>Suggested <span className="tag tag-ai">AI</span></h2></div>
          {/* Descriptions come from the curated 299-code list, not from the model. */}
          {codes.codes.length === 0 && <p className="empty">No codes matched.</p>}
          {codes.codes.map((c) => (
            <div key={c.code} className="row space listrow">
              <span><strong className="mono">{c.code}</strong> {c.description}</span>
              <button onClick={() => run('code', () => api.setCode(visit.id, c.code), 'icd10')}>Use</button>
            </div>
          ))}
          {codes.notes?.map((n, i) => <p key={i} className="muted small">{n}</p>)}
        </div>
      )}

      {/*
        Searching the list directly, for a code the assistant did not offer. Deliberately a
        search rather than a free-text field: the physician picks any of the 299 curated
        codes, but cannot invent one, and the description that lands on the chart is the
        official wording rather than something typed from memory.
      */}
      {/* Coding from here raises the path dialog too, same as the suggested list — the
          decision follows the code wherever it was picked. */}
      <CodePicker
        disabled={!visit.working_diagnosis}
        onPick={(code) => run('code', () => api.setCode(visit.id, code), 'icd10')}
      />
    </section>
  )
}

function CodePicker({ onPick, disabled }) {
  const [term, setTerm] = useState('')
  const [results, setResults] = useState([])
  const [total, setTotal] = useState(null)

  useEffect(() => {
    const t = setTimeout(() => {
      api.searchIcd10(term).then((d) => { setResults(d.codes); setTotal(d.total) }).catch(() => {})
    }, 200)
    return () => clearTimeout(t)
  }, [term])

  return (
    <div className="card">
      <div className="card-head">
        <h2>Choose a code yourself</h2>
        {total != null && <span className="muted small">{total} codes</span>}
      </div>

      <input value={term} onChange={(e) => setTerm(e.target.value)}
        placeholder="Search by code or description — J44, pneumonia, COVID…" />

      {results.length === 0 && <p className="empty small">No codes matched.</p>}

      {results.map((c) => (
        <div key={c.code} className="row space listrow">
          <span>
            <strong className="mono">{c.code}</strong> {c.description}
            {c.synonyms?.length > 0 && (
              <div className="muted small">also: {c.synonyms.join(', ')}</div>
            )}
          </span>
          <button disabled={disabled} onClick={() => onPick(c.code)}>Use</button>
        </div>
      ))}

      {disabled && <p className="muted small">Choose a working diagnosis first.</p>}
    </div>
  )
}

// --- choosing a path -------------------------------------------------------------------

/**
 * The fork after coding the diagnosis.
 *
 * Two routes exist in the workflow and this is where the physician picks one. Ordering
 * tests goes the long way round — investigations, results, then treatment. Going straight
 * to treatment skips both, and the sidebar greys them out afterwards to say so.
 *
 * It is not irreversible. From treatment selection the engine still allows ordering tests
 * later, so a doctor who chose one route and changed their mind is not stuck; they just go
 * back to Investigations and order something.
 */
export function ChoosePath({ visit, run, busy, can, onDone, treatmentOnly }) {
  const dx = visit.working_diagnosis

  /*
   * Offered whenever both routes are genuinely open, which is true twice: after coding the
   * first diagnosis, and again at results review, where the engine allows both ordering
   * more tests and moving on to treatment.
   *
   * Asked from what the engine permits rather than from the status name, so the second fork
   * did not have to be described separately — it is the same question.
   */
  const again = visit.status === 'RESULTS_REVIEW'

  if (!dx) {
    return (
      <div>
        <p className="empty">Record a working diagnosis first.</p>
        <button onClick={() => onDone?.('diagnosis')}>Go to diagnosis</button>
      </div>
    )
  }

  if (treatmentOnly && !again) {
    return (
      <div>
        <div className="note warn">
          This visit already went straight to treatment. Investigations and results are
          skipped — you can still order tests from the Investigations step if that changes.
        </div>
        <div className="form-actions">
          <button onClick={() => onDone?.('treatment')}>Go to treatment</button>
          <button onClick={() => onDone?.('investigations')}>Order tests after all</button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <p className="muted small">
        <strong>{dx.label}</strong>{' '}
        <span className="mono">{dx.icd10_code || 'no code'}</span>
      </p>

      <div className="card">
        <div className="card-head">
          <h2>{again ? 'Order further tests' : 'Order investigations'}</h2>
        </div>
        <p className="small">
          {again
            ? 'More is needed before treating. Goes back to test selection; results already recorded are kept.'
            : 'Choose tests, upload and analyse the results, then decide whether they change the diagnosis before treating.'}
        </p>
        <button
          className="primary"
          disabled={busy || !can('TEST_SELECTION')}
          onClick={() => run('path', () => again ? api.orderMore(visit.id) : api.investigate(visit.id))
            .then((r) => { if (r) onDone?.('investigations') })}
        >
          {again ? 'Order more tests' : 'Investigate first'}
        </button>
      </div>

      <div className="card">
        <div className="card-head"><h2>Treat now</h2></div>
        <p className="small">
          {again
            ? 'The results are enough to act on. Goes to treatment.'
            : 'No tests needed. Goes directly to treatment; the investigations and results steps are skipped for this visit.'}
        </p>
        <button
          disabled={busy || !can('TREATMENT_SELECTION')}
          onClick={() => run('path', () => again ? api.treat(visit.id) : api.skipInvestigations(visit.id))
            .then((r) => { if (r) onDone?.('treatment') })}
        >
          Straight to treatment
        </button>
      </div>

      {/* Closing without choosing is allowed. The decision is still there to make, and the
          ICD-10 step keeps a button for reopening this. */}
      <p className="muted small">Close this to decide later.</p>
    </div>
  )
}

// --- investigations -------------------------------------------------------------------

export function Investigations({ visit, run, busy, can }) {
  const [advice, setAdvice] = useState(null)
  const [chosen, setChosen] = useState({})
  const [own, setOwn, clearOwn] = useDraft(`inv-own.${visit.id}`, [])
  const [draft, setDraft] = useDraft(`inv-draft.${visit.id}`, { name: '', category: 'other' })

  const picked = advice?.investigations?.filter((i) => chosen[i.name]) ?? []
  const total = picked.length + own.length

  const addOwn = () => {
    const name = draft.name.trim()
    if (!name) return
    setOwn([...own, { name, category: draft.category, rationale: null }])
    setDraft({ name: '', category: 'other' })
  }

  /**
   * Order what has been ticked.
   *
   * The engine needs the visit at TEST_SELECTION before it will accept an order, and
   * getting there is a transition of its own. Doing it here rather than making the doctor
   * press two buttons in the right sequence: the second press carried no decision, it was
   * only the interface exposing the state machine's shape as a chore.
   */
  const order = async () => {
    const list = [
      ...picked.map((i) => ({ name: i.name, category: i.category ?? 'other', rationale: i.rationale ?? null })),
      ...own,
    ]
    if (!list.length) return

    if (visit.status !== 'TEST_SELECTION') {
      const moved = await run('order', () =>
        visit.status === 'RESULTS_REVIEW' ? api.orderMore(visit.id) : api.investigate(visit.id))
      if (!moved) return   // refused, and the error is already on screen
    }

    const result = await run('order', () => api.orderInvestigations(visit.id, list), 'investigations')
    if (result) { setChosen({}); setOwn([]); clearOwn() }
  }

  return (
    <section>
      <AiHeading action={
        <button disabled={busy || !visit.working_diagnosis} onClick={() => run('inv', async () => {
          setAdvice(await api.suggestInvestigations(visit.id)); return null
        })}>{busy === 'inv' ? 'Thinking…' : 'Suggest tests'}</button>
      }>Investigations</AiHeading>

      {visit.ordered_investigations?.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Ordered</h2></div>
          {visit.ordered_investigations.map((i, k) => (
            <div key={k} className="row space listrow">
              <span>{i.name} <span className="muted small">({i.category})</span></span>
              <span className={i.status === 'resulted' ? 'pill ok' : 'pill warn'}>{i.status}</span>
            </div>
          ))}
        </div>
      )}

      {/* Tick what you want. Ordering everything the model proposed is not decision support. */}
      {advice?.investigations?.map((i, k) => (
        <label key={k} className="card check">
          <input type="checkbox" checked={!!chosen[i.name]}
            onChange={() => setChosen({ ...chosen, [i.name]: !chosen[i.name] })} />
          <span>
            <strong>{i.name}</strong> <span className="pill">{i.category}</span>
            <div className="small">{i.rationale}</div>
            {i.changes_management && (
              <div className="small muted">Changes management: {i.changes_management}</div>
            )}
          </span>
        </label>
      ))}

      {/* The doctor's own tests, always available — the assistant's list is a starting
          point, not the menu. */}
      <div className="card">
        <div className="card-head"><h2>Order your own</h2></div>
        <div className="row wrap">
          <input style={{ flex: 1, minWidth: 200 }} value={draft.name}
            placeholder="Test name, e.g. D-dimer"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && addOwn()} />
          <select value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
            <option value="other">category…</option>
            <option value="laboratory">laboratory</option>
            <option value="radiology">radiology</option>
          </select>
          <button onClick={addOwn} disabled={!draft.name.trim()}>Add</button>
        </div>
        <p className="muted small">
          The category is corrected by the engine from the test's name when it can — a chest
          X-ray files as radiology whatever is picked here.
        </p>

        {own.map((i, k) => (
          <div key={k} className="row space listrow">
            <span><strong>{i.name}</strong> <span className="pill">{i.category}</span></span>
            <button className="danger" onClick={() => setOwn(own.filter((_, j) => j !== k))}>
              Remove
            </button>
          </div>
        ))}
      </div>

      {(can('TEST_SELECTION') || visit.status === 'TEST_SELECTION') && (
        <div className="card">
          <div className="form-actions">
            <button className="primary" disabled={busy || total === 0} onClick={order}>
              {busy === 'order'
                ? 'Ordering…'
                : `Order ${total} test${total === 1 ? '' : 's'}`}
            </button>
            <button disabled={busy} onClick={() => run('skip', () =>
              visit.status === 'TEST_SELECTION'
                ? api.skipInvestigations(visit.id)
                : api.skipInvestigations(visit.id)
            )}>Order nothing — go to treatment</button>
          </div>
          {total === 0 && (
            <p className="muted small">Tick a suggestion or add your own, then order.</p>
          )}
        </div>
      )}
    </section>
  )
}

// --- results ----------------------------------------------------------------------------

/**
 * Three separate acts, kept as three numbered steps:
 *   upload   — transcribes the document, interprets nothing
 *   analyse  — the physician asks what it says
 *   add      — puts the analysis into this visit, so re-assessment reasons with it
 *
 * Only analysed reports can be added. A transcription nobody has interpreted contributes
 * nothing, and the engine refuses it rather than reasoning over raw numbers.
 */
export function Results({ visit, run, busy, patientId, can, setError, refreshStates, investigations, goTo, startReassessing }) {
  const [reports, setReports] = useState([])
  const [typed, setTyped, clearTyped] = useDraft(`typed-result.${visit.id}`, '')
  const [editingResults, setEditingResults] = useState(false)
  const [draftResults, setDraftResults] = useState(visit.results_summary ?? '')
  const [working, setWorking] = useState(null)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [elapsed, setElapsed] = useState(0)
  const [chosen, setChosen] = useState({})

  const uploading = working === 'upload'
  const analysing = working === 'analyse'

  /**
   * Only this visit's documents.
   *
   * A patient's earlier reports are already part of the chart the assistant reads; listing
   * them here as things to analyse and add would invite folding a report from March into a
   * consultation in August. This step is about what came back for *these* tests.
   */
  const refresh = useCallback(() => {
    api.reports(patientId)
      .then((d) => setReports(d.reports.filter((r) => r.visit_id === visit.id)))
      .catch(setError)
  }, [patientId, visit.id])

  useEffect(() => { refresh() }, [refresh])

  // Re-seed from the record whenever it changes, unless the doctor is mid-edit — replacing
  // their text under them would be exactly the kind of loss the drafts exist to prevent.
  useEffect(() => {
    if (!editingResults) setDraftResults(visit.results_summary ?? '')
  }, [visit.results_summary, editingResults])

  // From the API rather than from the engine payload: these carry database ids.
  const ordered = investigations ?? []

  // What analysis would run over: uploaded to *this* visit, and not yet interpreted. A
  // report from a previous encounter is already part of the chart and is not re-read here.
  const pending = reports.filter((r) => r.visit_id === visit.id && !r.analysed)

  /**
   * Upload and transcribe.
   *
   * Slow, unavoidably: the model reads the page, which takes about a minute for a scan and
   * roughly that per page for a PDF. The elapsed counter is not decoration — without it a
   * ninety-second wait is indistinguishable from a hang, and the doctor reloads and loses
   * the work that was nearly finished.
   */
  const upload = async (file, investigationId = null) => {
    if (!file) return
    setWorking('upload')
    setElapsed(0)
    setError(null)

    const started = Date.now()
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000)

    try {
      await api.uploadReport(patientId, file, visit.id, investigationId)
      refresh()
    } catch (e) {
      setError(e)
    } finally {
      clearInterval(tick)
      setWorking(null)
      setElapsed(0)
    }
  }

  /**
   * Interpret what has been uploaded to this visit.
   *
   * Runs over the pending set — one report or several — because a physician who has just
   * uploaded a chest film and a CBC wants both read, not two identical decisions.
   *
   * Sequential rather than parallel: these are model calls on a free tier, and firing three
   * at once is the reliable way to be rate-limited into failing all three. One at a time
   * also means a failure halfway through leaves the earlier ones analysed rather than
   * losing the batch.
   */
  const analyseAll = async (subset = null) => {
    const queue = Array.isArray(subset) ? subset : pending
    if (queue.length === 0) return

    setWorking('analyse')
    setProgress({ done: 0, total: queue.length })
    setError(null)

    try {
      for (const [i, report] of queue.entries()) {
        setProgress({ done: i, total: queue.length })
        await api.analyseReport(report.id)
      }
    } catch (e) {
      setError(e)
    } finally {
      setWorking(null)
      setProgress({ done: 0, total: 0 })
      refresh()
    }
  }

  // Analysed, on this visit, and not already folded into the record. `report_ids` is what
  // the engine has actually reasoned over, so re-adding the same result is not offered.
  const alreadyAdded = reports.filter((r) => (visit.report_ids ?? []).includes(r.id))
  const addable = reports.filter(
    (r) => r.visit_id === visit.id && r.analysed && !(visit.report_ids ?? []).includes(r.id)
  )

  // Ticked unless explicitly unticked. A doctor who analysed a result almost always wants
  // it in the visit; making them tick it again to enable the button is a step that carries
  // no decision.
  const selected = addable.filter((r) => chosen[r.id] !== false)

  // WAITING_FOR_TESTS can move to RESULTS_REVIEW; once there, more results can still be
  // added — they arrive in batches, and the engine treats a repeat as an append.
  const canAddResults = can('RESULTS_REVIEW') || visit.status === 'RESULTS_REVIEW'

  // Lines already in the visit summary, so the doctor can see what has been logged by hand
  // without scrolling back up.
  const typedResults = (visit.results_summary ?? '').split('\n').filter((l) => l.trim())

  const addSelected = () => {
    if (selected.length === 0) return
    return run('add', () => api.resultsFromReports(visit.id, selected.map((r) => r.id)))
      .then((r) => { if (r) { setChosen({}); refreshStates() } })
  }

  return (
    <section>
      <div className="page-head"><h2>Results & reports</h2></div>

      {visit.status === 'WAITING_FOR_TESTS' && (
        <div className="note warn">
          Waiting for tests. Upload the results, analyse them, then add them to this visit.
        </div>
      )}

      <div className="card">
        <div className="card-head"><h2>1 · Upload</h2></div>
        <p className="muted small">Transcribes what is printed. Nothing is interpreted yet.</p>

        {/*
          One upload slot per ordered test, so a result is filed against the thing it
          answers. A visit waiting on three tests with two PDFs attached should be able to
          say which two — otherwise "outstanding" is a guess.
        */}
        {ordered.length > 0 ? ordered.map((inv) => {
          const attached = reports.filter((r) => r.investigation_id === inv.id)
          return (
            <div key={inv.id} className="listrow">
              <div className="row space">
                <span>
                  <strong>{inv.name}</strong>{' '}
                  <span className="pill">{inv.category}</span>
                </span>
                <span className={attached.length ? 'pill ok' : 'pill warn'}>
                  {attached.length ? `${attached.length} uploaded` : 'awaiting result'}
                </span>
              </div>
              {attached.map((r) => (
                <div key={r.id} className="small muted">{r.label}</div>
              ))}
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp"
                disabled={!!working}
                onChange={(e) => upload(e.target.files?.[0], inv.id)} />
            </div>
          )
        }) : (
          <p className="empty small">No tests ordered on this visit.</p>
        )}

        <div className="listrow">
          <h4>Something nobody ordered</h4>
          <p className="muted small">
            Results arrive late, out of order, and sometimes for tests nobody ordered. This
            files against the visit but not against a test.
          </p>
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" disabled={!!working}
            onChange={(e) => upload(e.target.files?.[0])} />
        </div>

        {uploading && (
          <div className="note warn">
            <strong>Reading the document — {elapsed}s elapsed.</strong>
            <div className="small">
              The model transcribes the page itself, which takes about a minute for a scan
              and roughly that per page for a PDF. Leave this open; navigating away cancels
              the upload.
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>2 · Analyse</h2>
          <button
            className="primary"
            // Held shut while a document is still being read. Analysing halfway through an
            // upload would run over a set that is about to change, and the report still
            // being transcribed would silently be left out.
            disabled={uploading || analysing || pending.length === 0}
            // Wrapped, not passed directly: React would hand the click event in as the
            // subset, and the batch would run over an event object instead of the reports.
            onClick={() => analyseAll()}
          >
            {analysing
              ? `Analysing ${progress.done + 1} of ${progress.total}…`
              : uploading
                ? 'Reading document…'
                : pending.length === 0
                  ? 'Nothing to analyse'
                  : `Analyse ${pending.length} ${pending.length === 1 ? 'report' : 'reports'}`}
          </button>
        </div>

        <p className="muted small">
          The physician's explicit act, and it runs over what has been uploaded to this
          visit — one report or several. Out-of-range flagging is arithmetic done in code; a
          flag the laboratory printed beats anything computed.
        </p>

        {uploading && (
          <div className="note warn">
            Reading a document ({elapsed}s). Analysis waits until the transcription is finished.
          </div>
        )}

        {reports.length === 0 && <p className="empty">No reports uploaded.</p>}

        {reports.map((r) => (
          <div key={r.id} className="row space listrow">
            <span>
              {r.label}{' '}
              {r.investigation && <span className="pill">{r.investigation}</span>}{' '}
              {r.visit_id === visit.id && <span className="pill info">this visit</span>}
            </span>
            <span className="row">
              <span className={r.analysed ? 'pill ok' : 'pill warn'}>
                {r.analysed ? 'analysed' : 'not analysed'}
              </span>
              {!r.analysed && (
                <button disabled={uploading || analysing} onClick={() => analyseAll([r])}>
                  {working === r.id ? 'Analysing…' : 'Analyse just this'}
                </button>
              )}
            </span>
          </div>
        ))}

        {pending.length === 0 && reports.length > 0 && (
          <p className="muted small">Everything uploaded to this visit has been analysed.</p>
        )}
      </div>

      <div className="card">
        <div className="card-head"><h2>3 · Add to this visit</h2></div>
        <p className="muted small">
          Puts the analysis into the record so the next assessment reasons with it. Only
          analysed reports can be added.
        </p>

        {/*
          The list lives beside the button that acts on it. It used to rely on ticking a box
          up in step 2, which left this button looking broken — disabled, with no indication
          of what it wanted.
        */}
        {addable.length === 0 ? (
          <p className="empty small">
            {alreadyAdded.length > 0
              ? 'Everything analysed has already been added to this visit.'
              : 'Nothing to add yet — upload a result and analyse it first.'}
          </p>
        ) : (
          addable.map((r) => (
            <label key={r.id} className="check listrow">
              <input type="checkbox" checked={chosen[r.id] !== false}
                onChange={() => setChosen({ ...chosen, [r.id]: chosen[r.id] === false })} />
              <span>
                {r.label}
                {r.investigation && <> — <strong>{r.investigation}</strong></>}
              </span>
            </label>
          ))
        )}

        <button
          className="primary"
          disabled={busy || !canAddResults || selected.length === 0}
          onClick={addSelected}
        >
          {busy === 'add'
            ? 'Adding…'
            : `Add ${selected.length || ''} result${selected.length === 1 ? '' : 's'}`.replace('  ', ' ')}
        </button>

        {/* Say why, rather than leaving a dead button to be puzzled over. */}
        {!canAddResults && (
          <p className="muted small">Available once tests have been ordered.</p>
        )}

        {alreadyAdded.length > 0 && (
          <p className="muted small">
            Already in this visit: {alreadyAdded.map((r) => r.label).join(', ')}.
          </p>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Type a result</h2>
          {typedResults.length > 0 && (
            <span className="pill">{typedResults.length} recorded by hand</span>
          )}
        </div>
        <p className="muted small">
          For a result that arrived on paper, over the phone, or one the reader could not
          make sense of. Recorded exactly as written — nothing interprets it.
        </p>
        <div className="form">
          {/* Draft-backed like every other form here: a typed result is often the fallback
              after a failed upload, and losing it to a stray click would be the second
              disappointment in a row. */}
          <textarea rows={3} value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder="D-dimer 140 mg/mL DDU (ref < 243). Chest X-ray: right lower lobe consolidation." />
          {/*
            Gated on `canAddResults`, not on `can('RESULTS_REVIEW')`. The old check went
            false the moment the first result landed, so a doctor could record one result
            and then never another — exactly wrong for results that arrive one at a time.
          */}
          <button disabled={busy || !typed.trim() || !canAddResults}
            onClick={() => run('results', () => api.recordResults(visit.id, typed))
              .then((r) => { if (r) { setTyped(''); clearTyped(); refreshStates() } })}>
            Record this result
          </button>
        </div>
        {!canAddResults && (
          <p className="muted small">Available once tests have been ordered.</p>
        )}
      </div>

      {visit.results_summary && (
        <>
          <div className="card">
            <div className="card-head">
              <h2>Recorded in this visit</h2>
              <button onClick={() => setEditingResults((v) => !v)}>
                {editingResults ? 'Cancel' : 'Edit'}
              </button>
            </div>

            {editingResults ? (
              <div className="form">
                {/*
                  Correcting what the results say, before deciding what they mean. Recording
                  appends — three uploads leave three blocks of machine output — so this is
                  where a typo gets fixed or that gets tidied into something a colleague can
                  read. It does not move the visit.
                */}
                <textarea
                  rows={Math.min(20, Math.max(4, (draftResults || '').split('\n').length + 1))}
                  value={draftResults}
                  onChange={(e) => setDraftResults(e.target.value)}
                />
                <p className="muted small">
                  Editing replaces what is recorded. The previous text is written to the
                  audit log first, so nothing is lost.
                </p>
                <div className="form-actions">
                  <button className="primary" disabled={busy || !draftResults.trim()}
                    onClick={() => run('amend', () => api.amendResults(visit.id, draftResults))
                      .then((r) => { if (r) setEditingResults(false) })}>
                    {busy === 'amend' ? 'Saving…' : 'Save corrected results'}
                  </button>
                  <button onClick={() => {
                    setDraftResults(visit.results_summary ?? '')
                    setEditingResults(false)
                  }}>Discard changes</button>
                </div>
              </div>
            ) : (
              <pre className="block">{visit.results_summary}</pre>
            )}
          </div>

          {/*
            The question the results exist to answer.
            Asked explicitly rather than left implicit, because "the bloods are back" and
            "the bloods changed my mind" are different conclusions, and only the doctor can
            say which. Both branches stay open afterwards — this records a direction, not a
            commitment.
          */}
          <div className="card">
            <div className="card-head"><h2>Do these results change the diagnosis?</h2></div>
            <p className="muted small">
              Current working diagnosis:{' '}
              <strong>{visit.working_diagnosis?.label ?? 'none recorded'}</strong>
            </p>

            <div className="form-actions">
              <button
                className="primary"
                disabled={busy || !can('TREATMENT_SELECTION')}
                onClick={() => run('treat', () => api.treat(visit.id), 'results')}
              >
                No — go to treatment
              </button>

              <button
                disabled={busy}
                onClick={() => { startReassessing?.(); goTo('differential') }}
              >
                Yes — reconsider
              </button>
            </div>

            <p className="muted small">
              Reconsidering re-runs the assistant with the results now in the chart, and the
              diagnosis can be revised from the Diagnosis step. Ordering further tests is
              available from Investigations.
            </p>
          </div>
        </>
      )}
    </section>
  )
}

// --- treatment ----------------------------------------------------------------------------

export function Treatment({ visit, run, busy, can }) {
  const [advice, setAdvice] = useState(null)
  const [chosen, setChosen] = useState({})
  const [own, setOwn, clearOwn] = useDraft(`rx-own.${visit.id}`, [])
  const [draft, setDraft] = useDraft(`rx-draft.${visit.id}`,
    { name: '', dose: '', frequency: '', duration: '' })
  const [ownWarnings, setOwnWarnings] = useState({ blocked: [], cautions: [] })

  const picked = (advice?.medications ?? []).filter((m) => chosen[m.name])
  const all = [...picked, ...own]

  /**
   * Add a drug the physician chose themselves, and screen it.
   *
   * The screen does not refuse — the physician is the decision-maker, and a system that
   * blocked their prescription would be a different kind of product. But a prescriber
   * working from memory can miss an allergy someone else recorded months ago, so the same
   * check the assistant's suggestions go through runs here and reports what it found.
   */
  const addOwn = async () => {
    const name = draft.name.trim()
    if (!name) return
    const next = [...own, { ...draft, name }]
    setOwn(next)
    setDraft({ name: '', dose: '', frequency: '', duration: '' })

    try {
      setOwnWarnings(await api.checkMedications(visit.id, next.map((m) => m.name)))
    } catch { /* the screen is advisory; failing it must not block prescribing */ }
  }

  const warningsFor = (name) => [
    ...ownWarnings.blocked.filter((w) => w.medication === name).map((w) => ({ ...w, blocked: true })),
    ...ownWarnings.cautions.filter((w) => w.medication === name).map((w) => ({ ...w, blocked: false })),
  ]

  return (
    <section>
      <AiHeading action={
        <button disabled={busy || !visit.working_diagnosis} onClick={() => run('meds', async () => {
          setAdvice(await api.suggestMedications(visit.id)); return null
        })}>{busy === 'meds' ? 'Thinking…' : 'Suggest treatment'}</button>
      }>Treatment</AiHeading>

      {can('TREATMENT_SELECTION') && (
        <button className="primary" onClick={() => run('treat', () =>
          visit.status === 'ICD10_SELECTION' ? api.skipInvestigations(visit.id) : api.treat(visit.id)
        )}>Move to treatment selection</button>
      )}

      {/*
        The conflict alert. Shown before the options, because a prescriber who reads the
        list first has already started deciding.
        `withheld` is a drug in a class the patient is recorded as allergic to: removed from
        the suggestions entirely, since a recommendation the doctor has to notice and reject
        is still a recommendation. `cautions` are interactions and conditions — still
        offered, because those are prescribing decisions rather than prohibitions.
      */}
      {advice && (advice.withheld?.length > 0 || advice.cautions?.length > 0) && (
        <div className={advice.withheld?.length ? 'note bad' : 'note warn'}>
          <strong>
            {advice.withheld?.length > 0
              ? `⚠ ${advice.withheld.length} medication${advice.withheld.length === 1 ? '' : 's'} withheld — allergy conflict`
              : `⚠ ${advice.cautions.length} caution${advice.cautions.length === 1 ? '' : 's'}`}
          </strong>

          {advice.withheld?.length > 0 && (
            <ul className="small">
              {advice.withheld.map((w, i) => (
                <li key={i}><strong>{w.medication}</strong> — {w.reason}</li>
              ))}
            </ul>
          )}

          {advice.cautions?.length > 0 && (
            <>
              <div className="small" style={{ marginTop: 8 }}><strong>Use with caution</strong></div>
              <ul className="small">
                {advice.cautions.map((w, i) => (
                  <li key={i}><strong>{w.medication}</strong> — {w.reason}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {advice?.medications?.length === 0 && (
        <div className="card">
          <p className="empty">
            Nothing could be offered. Everything suggested was withheld — see above.
          </p>
        </div>
      )}

      {advice?.medications?.map((m, i) => {
        const cautions = (advice.cautions ?? []).filter((c) => c.medication === m.name)
        return (
          <label key={i} className={`card check ${cautions.length ? 'flagged' : ''}`}>
            <input type="checkbox" checked={!!chosen[m.name]}
              onChange={() => setChosen({ ...chosen, [m.name]: !chosen[m.name] })} />
            <span style={{ flex: 1 }}>
              <strong>{m.name}</strong>{' '}
              <span className="muted">{[m.dose, m.frequency, m.duration].filter(Boolean).join(' · ')}</span>
              {m.rationale && <div className="small">{m.rationale}</div>}
              {cautions.map((c, j) => (
                <div key={j} className="note warn small">⚠ {c.reason}</div>
              ))}
            </span>
          </label>
        )
      })}

      {advice?.non_drug_advice?.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Non-drug advice</h2></div>
          <ul className="small">{advice.non_drug_advice.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}

      {/* The physician's own prescriptions, always available. */}
      <div className="card">
        <div className="card-head"><h2>Prescribe your own</h2></div>
        <div className="form-row">
          <input value={draft.name} placeholder="Medication"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <input value={draft.dose} placeholder="Dose"
            onChange={(e) => setDraft({ ...draft, dose: e.target.value })} />
          <input value={draft.frequency} placeholder="Frequency"
            onChange={(e) => setDraft({ ...draft, frequency: e.target.value })} />
          <input value={draft.duration} placeholder="Duration"
            onChange={(e) => setDraft({ ...draft, duration: e.target.value })} />
        </div>
        <div className="row">
          <button onClick={addOwn} disabled={!draft.name.trim()}>Add</button>
          <span className="muted small">Checked against the patient's allergies and current medications.</span>
        </div>

        {own.map((m, k) => {
          const warnings = warningsFor(m.name)
          const blocked = warnings.some((w) => w.blocked)
          return (
            <div key={k} className={`listrow ${blocked ? 'flagged-row' : ''}`}>
              <div className="row space">
                <span>
                  <strong>{m.name}</strong>{' '}
                  <span className="muted">{[m.dose, m.frequency, m.duration].filter(Boolean).join(' · ')}</span>
                </span>
                <button className="danger" onClick={() => {
                  setOwn(own.filter((_, j) => j !== k))
                }}>Remove</button>
              </div>
              {/* Reported, not enforced. The physician decides; they should not decide
                  without being told. */}
              {warnings.map((w, j) => (
                <div key={j} className={w.blocked ? 'note bad small' : 'note warn small'}>
                  {w.blocked ? '⛔ Conflict: ' : '⚠ Caution: '}{w.reason}
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {visit.status === 'TREATMENT_SELECTION' && (
        <div className="card">
          {own.some((m) => warningsFor(m.name).some((w) => w.blocked)) && (
            <div className="note bad">
              One of the medications you added conflicts with a recorded allergy. Prescribing
              it is your decision — this is a warning, not a refusal.
            </div>
          )}
          <button className="primary" disabled={busy || all.length === 0}
            onClick={() => run('prescribe', () => api.prescribe(visit.id, all.map((m) => ({
              name: m.name,
              dose: m.dose || null,
              frequency: m.frequency || null,
              duration: m.duration || null,
              rationale: m.rationale ?? null,
            })), 'treatment')).then((r) => { if (r) { setChosen({}); setOwn([]); clearOwn() } })}>
            Prescribe {all.length} medication{all.length === 1 ? '' : 's'}
          </button>
        </div>
      )}

      {visit.prescribed_medications?.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Prescribed</h2></div>
          {visit.prescribed_medications.map((m, i) => (
            <div key={i} className="listrow small">
              <strong>{m.name}</strong>{' '}
              {[m.dose, m.frequency, m.duration].filter(Boolean).join(' · ')}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// --- soap and closing ----------------------------------------------------------------------

export function Soap({ visit, persisted, goTo }) {
  if (!persisted) {
    return <p className="empty">Available once the visit is part of the record.</p>
  }

  return (
    <section>
      <div className="page-head"><h2>SOAP note</h2></div>
      <p className="muted small">
        Assembled from the record — no model is involved. Review it, correct anything
        wrong, and save; only then does it enter the patient's profile.
      </p>
      {/* Same component the patient profile uses, so the note read afterwards is the
          document seen during the consultation. */}
      <SoapNote visitId={visit.id} editable />
    </section>
  )
}

export function CloseVisit({ visit, run, busy, can }) {
  const [plan, setPlan] = useState('')
  const [note, setNote] = useState('')

  return (
    <section>
      <div className="page-head"><h2>Close visit</h2></div>

      {visit.status === 'COMPLETED' && <div className="note ok">This visit is closed.</div>}

      <div className="card">
        <div className="card-head"><h2>Note</h2></div>
        <div className="form">
          <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          <button disabled={busy || !note.trim()}
            onClick={() => run('note', () => api.addNote(visit.id, note)).then(() => setNote(''))}>
            Add note
          </button>
        </div>
      </div>

      {can('FOLLOW_UP') && (
        <div className="card">
          <div className="card-head"><h2>Follow-up</h2></div>
          <div className="form">
            <label>Plan
              <input value={plan} onChange={(e) => setPlan(e.target.value)}
                placeholder="Review in 6 weeks with spirometry" />
            </label>
            <button disabled={busy}
              onClick={() => run('followup', () => api.followUp(visit.id, plan || null))}>
              Schedule follow-up
            </button>
          </div>
        </div>
      )}

      {can('COMPLETED') && (
        <div className="card">
          <button className="primary" disabled={busy}
            onClick={() => run('complete', () => api.complete(visit.id))}>
            Complete visit
          </button>
          {/* Terminal, and worth saying before the click rather than after. */}
          <p className="muted small">
            A completed visit cannot be reopened. A new concern is a new visit.
          </p>
        </div>
      )}

      {visit.doctor_notes && (
        <div className="card">
          <div className="card-head"><h2>Notes</h2></div>
          <pre className="block">{visit.doctor_notes}</pre>
        </div>
      )}
    </section>
  )
}
