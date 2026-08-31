import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'

/**
 * What the assistant is allowed to reason from.
 *
 * Worth having a screen for even before anyone adds anything. A doctor reading a
 * differential that cites GOLD 2026 should be able to find out what else was in scope —
 * and, just as usefully, what was not. The corpus is thin on pharyngitis and sinusitis, and
 * a list that shows five respiratory guidelines makes that visible in a way a citation
 * never does.
 *
 * The curated documents are shown but cannot be touched. That is not a disabled button
 * being polite: there is no API route that could remove them.
 */
export default function ReferencesPage() {
  const { user } = useAuth()
  const [documents, setDocuments] = useState(null)
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)

  /*
   * Reading is everyone's; changing is the administrator's.
   *
   * An added document becomes evidence the assistant quotes into every clinician's
   * differentials, not just the adder's — closer to a configuration change than to
   * clinical work. The API enforces this independently; hiding the buttons only avoids
   * offering a doctor a door that will refuse them.
   */
  const canEdit = user.role === 'admin'

  const refresh = useCallback(() => {
    api.references()
      .then((d) => { setDocuments(d.documents); setError(null) })
      .catch(setError)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  if (!documents) {
    return error
      ? <div className="page"><div className="note bad">{error.message}</div></div>
      : <p className="empty">Loading the reference library…</p>
  }

  const added = documents.filter((d) => d.origin === 'added')
  const curated = documents.filter((d) => d.origin !== 'added')
  const passages = documents.reduce((n, d) => n + d.chunks, 0)

  return (
    <div className="page">
      <div className="page-head">
        <div />
        {canEdit && (
          <button className="primary" onClick={() => setModal({ kind: 'add' })}>
            Add a document
          </button>
        )}
      </div>

      {error && <div className="note bad">{error.message}</div>}

      <p className="muted small">
        {documents.length} documents, {passages.toLocaleString()} passages. The assistant
        retrieves from these and cites them — it cannot draw on anything that is not here.
      </p>

      <div className="card">
        <div className="card-head">
          <h2>Added by the clinic</h2>
          <span className="muted small">{added.length}</span>
        </div>

        {added.length === 0 && (
          <p className="empty small">
            None yet. A local protocol or a guideline the clinic follows can be added here
            by an administrator, and the assistant will cite it alongside the published
            sources.
          </p>
        )}

        {!canEdit && added.length > 0 && (
          <p className="muted small">
            Read-only. What the assistant may reason from is set by an administrator: a
            document added here is quoted into every clinician's differentials, not only
            the adder's.
          </p>
        )}

        {added.map((d) => (
          <div key={d.source_name} className="listrow">
            <div className="row space">
              <div>
                <strong>{d.title}</strong>{' '}
                <span className="pill info">{d.chunks} passages</span>
              </div>
              {canEdit && d.removable && (
                <button className="danger" onClick={() => setModal({ kind: 'remove', document: d })}>
                  Remove
                </button>
              )}
            </div>
            <div className="small muted">{d.citation}</div>
            <div className="small muted">
              {d.added_by ? <>Added by {d.added_by}</> : <em>Added by an account since removed</em>}
              {d.original_filename && <> · <span className="mono">{d.original_filename}</span></>}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Curated guidelines</h2>
          <span className="muted small">{curated.length}</span>
        </div>

        <p className="muted small">
          Chosen page by page, with only the sections holding clinical guidance ingested.
          These are fixed — they are the evidence base the assistant was built around, and
          nothing in the application can edit or remove them.
        </p>

        {curated.map((d) => (
          <div key={d.source_name} className="listrow">
            <div className="row space">
              <div>
                <strong>{d.title}</strong>{' '}
                <span className="pill info">{d.chunks} passages</span>
              </div>
              <span className="pill ok">fixed</span>
            </div>
            <div className="small muted">{d.citation}</div>
          </div>
        ))}
      </div>

      {modal?.kind === 'add' && (
        <Modal title="Add a reference document" onClose={() => setModal(null)}>
          <AddForm
            onCancel={() => setModal(null)}
            onAdded={() => { setModal(null); refresh() }}
          />
        </Modal>
      )}

      {modal?.kind === 'remove' && (
        <Modal title="Remove this document?" onClose={() => setModal(null)}>
          <p><strong>{modal.document.title}</strong></p>
          <p className="muted small">{modal.document.citation}</p>
          <div className="note warn">
            Its {modal.document.chunks} passages leave the assistant's evidence base
            immediately. Differentials already recorded keep the citations they were given —
            those are part of the record and are not rewritten.
          </div>
          <div className="form-actions">
            <button className="danger" onClick={async () => {
              try {
                await api.removeReference(modal.document.source_name)
                setModal(null)
                refresh()
              } catch (e) { setError(e); setModal(null) }
            }}>Remove it</button>
            <button onClick={() => setModal(null)}>Keep it</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

/**
 * The upload form.
 *
 * Says plainly that it will be slow, because it will be: the document is read, split and
 * embedded before this returns. A spinner with no explanation on a thirty-second wait reads
 * as a hang, and the second click makes it worse.
 */
function AddForm({ onAdded, onCancel }) {
  const [file, setFile] = useState(null)
  const [values, setValues] = useState({ title: '', publisher: '', year: '', reference: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const set = (k) => (e) => setValues({ ...values, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.addReference(file, values)
      onAdded()
    } catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}

      <label>Document
        <input
          type="file"
          accept=".pdf,.txt,.md"
          onChange={(e) => {
            const chosen = e.target.files?.[0] ?? null
            setFile(chosen)
            // The file name is a decent first guess at a title and saves retyping it.
            // Only as a default — it is overwritten only while the field is untouched.
            if (chosen && !values.title) {
              setValues((v) => ({ ...v, title: chosen.name.replace(/\.[^.]+$/, '') }))
            }
          }}
        />
      </label>
      <p className="muted small">
        PDF, plain text or Markdown, up to 10 MB. Added documents are read whole, so this
        is meant for a protocol or a short guideline — not a full 200-page report.
      </p>

      <label>Title
        <input value={values.title} onChange={set('title')}
          placeholder="Clinic Asthma Escalation Protocol" />
      </label>
      <p className="muted small">
        This is what the assistant cites, so write it the way you would want to see it in a
        differential.
      </p>

      <label>Publisher
        <input value={values.publisher} onChange={set('publisher')}
          placeholder="Respiratory Clinic" />
      </label>
      <label>Year
        <input value={values.year} onChange={set('year')} placeholder="2026" inputMode="numeric" />
      </label>
      <label>Reference
        <input value={values.reference} onChange={set('reference')} placeholder="v2.1" />
      </label>

      <div className="note warn">
        Whatever is added here becomes evidence the assistant retrieves and quotes into
        clinical suggestions. Add only documents you would stand behind — your name is
        recorded against it.
      </div>

      <div className="form-actions">
        <button className="primary" disabled={busy || !file || !values.title.trim()}>
          {busy ? 'Reading and embedding…' : 'Add to the library'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>

      {busy && (
        <p className="muted small">
          The document is being split and embedded now, so it is citable the moment this
          finishes. It can take a minute — leaving this open is fine.
        </p>
      )}
    </form>
  )
}
