import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { NewPatientForm } from '../forms/PatientForms'
import Filter from '../components/Filter'

/**
 * Everyone the clinic has on record.
 *
 * The search box asks the API, because it matches on fields the list does not show. The
 * rest of the filtering happens here on the returned page: sex, smoking status and visit
 * count are all already in hand, and a round trip to narrow a list you are looking at makes
 * the control feel slower than thinking.
 *
 * The counts under the filter bar say what was filtered out rather than silently showing
 * fewer rows. A list that quietly hides people is a list nobody can trust to be complete.
 */

const SORTS = {
  name: { label: 'Name (A–Z)', compare: (a, b) => a.full_name.localeCompare(b.full_name) },
  id: { label: 'Patient id', compare: (a, b) => a.id.localeCompare(b.id) },
  visits: { label: 'Most visits', compare: (a, b) => (b.visits_count ?? 0) - (a.visits_count ?? 0) },
  fewest: { label: 'Fewest visits', compare: (a, b) => (a.visits_count ?? 0) - (b.visits_count ?? 0) },
}

const BLANK = { sex: '', smoking: '', visits: '', sort: 'name' }

export default function PatientsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()

  const [patients, setPatients] = useState([])
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState(BLANK)
  const [registering, setRegistering] = useState(false)
  const [deleting, setDeleting] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.patients(search).then((d) => setPatients(d.patients.data ?? [])).catch(setError)
  }, [search])

  useEffect(() => {
    const t = setTimeout(refresh, 200)   // debounce the search box
    return () => clearTimeout(t)
  }, [refresh])

  const set = (key) => (value) => setFilters((f) => ({ ...f, [key]: value }))

  const shown = useMemo(() => {
    const kept = patients.filter((p) => {
      if (filters.sex && p.sex !== filters.sex) return false
      if (filters.smoking && p.smoking_status !== filters.smoking) return false

      const visits = p.visits_count ?? 0
      if (filters.visits === 'none' && visits !== 0) return false
      if (filters.visits === 'some' && visits === 0) return false
      if (filters.visits === 'many' && visits < 3) return false

      return true
    })

    return [...kept].sort(SORTS[filters.sort].compare)
  }, [patients, filters])

  const filtering = filters.sex || filters.smoking || filters.visits
  const hidden = patients.length - shown.length

  /**
   * Open a consultation, then navigate to its own URL.
   *
   * The visit is not in the database at this point — it is a draft the server holds until a
   * diagnosis is chosen — but it has an id, and giving it a URL means the back button and a
   * refresh both behave.
   */
  const consult = async (patient) => {
    try {
      const { visit } = await api.openConsultation(patient.id)
      navigate(`/consultations/${visit.id}`)
    } catch (e) { setError(e) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div />
        {user.role !== 'patient' && (
          <button className="primary" onClick={() => setRegistering(true)}>New patient</button>
        )}
      </div>

      {error && <div className="note bad">{error.message}</div>}

      <div className="filters">
        <div className="field grow">
          <label htmlFor="q">Search</label>
          <input id="q" placeholder="Name or patient id…" value={search}
            onChange={(e) => setSearch(e.target.value)} />
        </div>

        <div className="field">
          <label htmlFor="sex">Sex</label>
          <Filter id="sex" value={filters.sex} onChange={set('sex')} options={[
            { value: '', label: 'Any' },
            { value: 'female', label: 'Female' },
            { value: 'male', label: 'Male' },
            { value: 'other', label: 'Other' },
          ]} />
        </div>

        <div className="field">
          <label htmlFor="smoking">Smoking</label>
          <Filter id="smoking" value={filters.smoking} onChange={set('smoking')} options={[
            { value: '', label: 'Any' },
            { value: 'never', label: 'Never' },
            { value: 'former', label: 'Former' },
            { value: 'current', label: 'Current' },
            { value: 'unknown', label: 'Not recorded' },
          ]} />
        </div>

        <div className="field">
          <label htmlFor="visits">Visits</label>
          <Filter id="visits" value={filters.visits} onChange={set('visits')} options={[
            { value: '', label: 'Any' },
            { value: 'none', label: 'Never seen' },
            { value: 'some', label: 'Seen at least once' },
            { value: 'many', label: 'Three or more' },
          ]} />
        </div>

        <div className="field">
          <label htmlFor="sort">Sort by</label>
          <Filter
            id="sort" value={filters.sort} onChange={set('sort')}
            options={Object.entries(SORTS).map(([value, s]) => ({ value, label: s.label }))}
          />
        </div>

        {(filtering || search) && (
          <button onClick={() => { setFilters(BLANK); setSearch('') }}>Clear</button>
        )}
      </div>

      {/* What the filters are doing, said plainly rather than left to be inferred. */}
      <p className="filter-summary">
        Showing <strong>{shown.length}</strong> of {patients.length}
        {search && <> matching “{search}”</>}
        {hidden > 0 && <> · {hidden} hidden by filters</>}
      </p>

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Id</th><th>Name</th><th>Age</th><th>Sex</th>
                <th>Smoking</th><th className="right">Visits</th><th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">
                    {patients.length === 0
                      ? 'No patients found.'
                      : 'No patients match these filters.'}
                  </td>
                </tr>
              )}

              {shown.map((p) => (
                <tr key={p.id} onClick={() => navigate(`/patients/${p.id}`)}
                  style={{ cursor: 'pointer' }}>
                  <td className="mono small">{p.id}</td>
                  <td><strong>{p.full_name}</strong></td>
                  <td>{p.age ?? <span className="muted">—</span>}</td>
                  <td className="muted">{p.sex}</td>
                  <td className="muted small">
                    {p.smoking_status === 'unknown'
                      ? <em>not recorded</em>
                      : p.smoking_status}
                  </td>
                  <td className="right">{p.visits_count ?? 0}</td>
                  <td className="right" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => navigate(`/patients/${p.id}`)}>Profile</button>
                    {user.role === 'doctor' && (
                      <button className="primary" onClick={() => consult(p)}>Consult</button>
                    )}
                    {/* Asks before it acts. A destructive control one click from a list
                        row is the one place a misclick costs a whole record. */}
                    {user.role === 'admin' && (
                      <button className="danger" onClick={() => setDeleting(p)}>Delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {deleting && (
        <Modal title="Delete this patient?" onClose={() => setDeleting(null)}>
          <p>
            <strong>{deleting.full_name}</strong>{' '}
            <span className="mono muted">{deleting.id}</span>
            {deleting.visits_count > 0 && (
              <> · {deleting.visits_count} visit{deleting.visits_count === 1 ? '' : 's'}</>
            )}
          </p>

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
                await api.deletePatient(deleting.id)
                setDeleting(null)
                refresh()
              } catch (e) { setError(e); setDeleting(null) }
            }}>Delete the whole record</button>
            <button onClick={() => setDeleting(null)}>Keep it</button>
          </div>
        </Modal>
      )}

      {registering && (
        <Modal title="Register a patient" onClose={() => setRegistering(false)}>
          <NewPatientForm
            onCancel={() => setRegistering(false)}
            onCreated={(p) => { setRegistering(false); navigate(`/patients/${p.id}`) }}
          />
        </Modal>
      )}
    </div>
  )
}
