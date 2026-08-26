import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

/**
 * Visits that are still open.
 *
 * The way back into a consultation left waiting for results — a visit put on hold on Monday
 * and picked up on Thursday when the laboratory replies. The workflow always permitted the
 * return; for a while nothing in the interface could reach it.
 */
export default function InProgressPage() {
  const navigate = useNavigate()
  const [visits, setVisits] = useState(null)

  useEffect(() => { api.openVisits().then((d) => setVisits(d.visits)).catch(() => setVisits([])) }, [])

  if (!visits) return <p className="empty">Loading…</p>

  const waiting = visits.filter((v) => v.awaiting_results)
  const active = visits.filter((v) => !v.awaiting_results)

  return (
    <div className="page">
      <div className="page-head"><h1>In progress</h1></div>

      {visits.length === 0 && <div className="card"><p className="empty">No open visits.</p></div>}

      {waiting.length > 0 && (
        <>
          <h2>Waiting for results</h2>
          {waiting.map((v) => <VisitRow key={v.id} visit={v} navigate={navigate} />)}
        </>
      )}

      {active.length > 0 && (
        <>
          <h2>Open consultations</h2>
          {active.map((v) => <VisitRow key={v.id} visit={v} navigate={navigate} />)}
        </>
      )}
    </div>
  )
}

function VisitRow({ visit, navigate }) {
  return (
    <div className="card">
      <div className="row space">
        <div>
          <strong>{visit.patient.full_name}</strong>{' '}
          <span className="mono muted small">{visit.id}</span>
          <div className="small">
            {visit.chief_complaint || <em className="muted">No complaint recorded.</em>}
          </div>
          {visit.working_diagnosis && (
            <div className="small"><strong>{visit.working_diagnosis}</strong></div>
          )}
        </div>
        <div className="stack">
          <span className={visit.awaiting_results ? 'pill warn' : 'pill info'}>
            {visit.status.replace(/_/g, ' ')}
          </span>
          <button className="primary" onClick={() => navigate(`/consultations/${visit.id}`)}>
            Resume
          </button>
        </div>
      </div>
    </div>
  )
}
