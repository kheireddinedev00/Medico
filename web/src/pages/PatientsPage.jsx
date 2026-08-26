import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import Modal from '../components/Modal'
import { NewPatientForm } from '../forms/PatientForms'

export default function PatientsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [patients, setPatients] = useState([])
  const [search, setSearch] = useState('')
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.patients(search).then((d) => setPatients(d.patients.data ?? [])).catch(setError)
  }, [search])

  useEffect(() => {
    const t = setTimeout(refresh, 200)   // debounce the search box
    return () => clearTimeout(t)
  }, [refresh])

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
        <h1>Patients</h1>
        <div className="row">
          <input className="search" placeholder="Search name or id…" value={search}
            onChange={(e) => setSearch(e.target.value)} />
          {user.role !== 'patient' && (
            <button className="primary" onClick={() => setRegistering(true)}>New patient</button>
          )}
        </div>
      </div>

      {error && <div className="note bad">{error.message}</div>}

      <div className="card">
        <table>
          <thead>
            <tr><th>Id</th><th>Name</th><th>Visits</th><th className="right">Actions</th></tr>
          </thead>
          <tbody>
            {patients.length === 0 && (
              <tr><td colSpan={4} className="muted">No patients found.</td></tr>
            )}
            {patients.map((p) => (
              <tr key={p.id} className="clickable" onClick={() => navigate(`/patients/${p.id}`)}>
                <td className="mono">{p.id}</td>
                <td><strong>{p.full_name}</strong></td>
                <td>{p.visits_count}</td>
                <td className="right" onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => navigate(`/patients/${p.id}`)}>Profile</button>
                  {user.role === 'doctor' && (
                    <button className="primary" onClick={() => consult(p)}>Consult</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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
