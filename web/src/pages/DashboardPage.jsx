import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { BarList, Panel, Stat, Trend, prettify, priorityTone } from '../components/Charts'

/**
 * The first screen after signing in, and a different screen for each role.
 *
 * One route, three answers. "What should I be looking at?" is the same question for
 * everyone; the answer is not. A doctor wants their own queue and what they left
 * unfinished. A nurse wants the shape of the room. An administrator wants the clinic, in a
 * form that survives being printed and taken into a meeting.
 *
 * The scoping is the API's, not this page's — a doctor's figures are filtered by
 * `doctor_id` in the controller, so no front-end mistake can show one clinician another's
 * workload.
 */
export default function DashboardPage() {
  const { user } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.stats().then(setData).catch(setError)
  }, [])

  if (error) return <div className="page"><div className="note bad">{error.message}</div></div>
  if (!data) return <p className="empty">Loading your dashboard…</p>

  const shared = { stats: data.stats, user, generatedAt: data.generated_at }

  if (user.role === 'doctor') return <DoctorDashboard {...shared} />
  if (user.role === 'nurse') return <NurseDashboard {...shared} />
  if (user.role === 'admin') return <AdminDashboard {...shared} />

  return <div className="page"><p className="empty">No dashboard for this account.</p></div>
}

/* ------------------------------------------------------------------ doctor */

function DoctorDashboard({ stats, user }) {
  const navigate = useNavigate()

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Good day, {firstName(user.name)}</h1>
          <p className="page-lede">
            Your queue and your open work. Patients assigned to other doctors are not
            counted here.
          </p>
        </div>
        <button className="primary" onClick={() => navigate('/waiting-room')}>
          Go to my queue
        </button>
      </div>

      <div className="stat-grid">
        <Stat
          value={stats.waiting_for_me} label="Waiting for you"
          tone={stats.waiting_for_me > 0 ? 'warn' : undefined}
          foot={stats.in_consultation > 0 ? `${stats.in_consultation} with you now` : 'Nobody in the room'}
        />
        <Stat
          value={stats.awaiting_results} label="Awaiting results"
          tone={stats.awaiting_results > 0 ? 'info' : undefined}
          foot="Parked on a laboratory"
        />
        <Stat value={stats.open_visits} label="Open visits" foot="Not yet completed" />
        <Stat
          value={stats.completed_today} label="Completed today" tone="ok"
          foot={`${stats.completed_this_week} this week`}
        />
      </div>

      <div className="dash-grid">
        <Panel title="Next in your queue" count={stats.queue_preview?.length ?? 0}>
          {(!stats.queue_preview || stats.queue_preview.length === 0) && (
            <p className="empty small">
              Nobody assigned to you. The nurses assign patients from the shared queue.
            </p>
          )}

          {stats.queue_preview?.map((entry) => (
            <div key={entry.id} className="listrow">
              <div className="row space">
                <Link to={`/patients/${entry.patient_id}`}>{entry.patient}</Link>
                <div className="row">
                  <span className={`pill ${priorityTone({ label: entry.priority }) ?? ''}`}>
                    {entry.priority ?? 'not scored'}
                  </span>
                  {entry.status === 'in_consultation' && <span className="pill ok">with you</span>}
                </div>
              </div>
              <span className="muted small">Waiting since {timeOf(entry.waiting_since)}</span>
            </div>
          ))}
        </Panel>

        <Panel title="Your recent visits" count={stats.recent_visits?.length ?? 0}>
          {(!stats.recent_visits || stats.recent_visits.length === 0) && (
            <p className="empty small">No visits recorded yet.</p>
          )}

          {stats.recent_visits?.map((visit) => (
            <div key={visit.id} className="listrow">
              <div className="row space">
                <strong>{visit.patient}</strong>
                <span className={visit.status === 'COMPLETED' ? 'pill ok' : 'pill warn'}>
                  {prettify(visit.status)}
                </span>
              </div>
              <span className="small">
                {visit.diagnosis || <em className="muted">No diagnosis recorded</em>}
              </span>
              {visit.status !== 'COMPLETED' && (
                <div className="row">
                  <button onClick={() => navigate(`/consultations/${visit.id}`)}>Resume</button>
                </div>
              )}
            </div>
          ))}
        </Panel>

        <Panel title="What you diagnose most" count={`${stats.patients_seen} patients seen`}>
          <BarList rows={stats.diagnoses} empty="No diagnoses recorded yet." />
        </Panel>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- nurse */

function NurseDashboard({ stats }) {
  const navigate = useNavigate()

  const needsAttention = stats.vitals_pending > 0 || stats.unassigned > 0

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>The waiting room</h1>
          <p className="page-lede">
            The whole queue, shared by every nurse. Two of these numbers are the ones you
            can act on directly.
          </p>
        </div>
        <button className="primary" onClick={() => navigate('/waiting-room')}>Open the queue</button>
      </div>

      {/*
        Said out loud rather than left to be noticed. An unscored patient is not a low
        priority — nobody has measured them — and an unassigned one is in no doctor's list.
      */}
      {needsAttention && (
        <div className="note warn">
          {stats.vitals_pending > 0 && (
            <>
              <strong>{stats.vitals_pending}</strong> waiting with no vitals recorded — they
              are unscored, not low priority.
            </>
          )}
          {stats.vitals_pending > 0 && stats.unassigned > 0 && <br />}
          {stats.unassigned > 0 && (
            <>
              <strong>{stats.unassigned}</strong> not assigned to a doctor — nobody has been
              told about them yet.
            </>
          )}
        </div>
      )}

      <div className="stat-grid">
        <Stat value={stats.waiting} label="Waiting now"
          foot={`${stats.in_consultation} with a doctor`} />
        <Stat value={stats.vitals_pending} label="Vitals pending"
          tone={stats.vitals_pending > 0 ? 'warn' : 'ok'} foot="Unscored until measured" />
        <Stat value={stats.unassigned} label="No doctor assigned"
          tone={stats.unassigned > 0 ? 'bad' : 'ok'} foot="In nobody's queue" />
        <Stat value={stats.arrived_today} label="Arrived today"
          foot={`${stats.seen_today} seen, ${stats.registered_today} newly registered`} />
      </div>

      <div className="dash-grid">
        <Panel title="Queue by priority">
          <BarList rows={stats.priorities} tone={priorityTone}
            empty="Nobody is waiting." />
        </Panel>

        <Panel title="Queue by doctor">
          <BarList rows={stats.by_doctor}
            tone={(r) => (r.label === 'Unassigned' ? 'bad' : undefined)}
            empty="Nobody is waiting." />
        </Panel>

        <Panel title="Longest waits" count={stats.longest_waits?.length ?? 0}>
          {(!stats.longest_waits || stats.longest_waits.length === 0) && (
            <p className="empty small">Nobody is waiting.</p>
          )}

          {stats.longest_waits?.map((entry) => (
            <div key={entry.id} className="listrow">
              <div className="row space">
                <strong>{entry.patient}</strong>
                <span className={`pill ${priorityTone({ label: entry.priority }) ?? ''}`}>
                  {entry.priority ?? 'not scored'}
                </span>
              </div>
              <span className="muted small">
                Since {timeOf(entry.arrived_at)} ·{' '}
                {entry.doctor ? `for ${entry.doctor}` : <em>no doctor assigned</em>}
              </span>
            </div>
          ))}
        </Panel>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- admin */

function AdminDashboard({ stats, generatedAt }) {
  const { people, activity, assistant } = stats

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Clinic overview</h1>
          <p className="page-lede">
            Everything the record can account for. Counted live rather than cached, so these
            figures cannot disagree with the tables they summarise.
          </p>
        </div>
        <button className="primary no-print" onClick={() => window.print()}>
          Print this report
        </button>
      </div>

      {/* Only on paper, where the screen's context is gone. */}
      <div className="print-only">
        <p className="small">
          Medico — clinic report, generated {new Date(generatedAt).toLocaleString()}.
        </p>
      </div>

      <h2>People</h2>
      <div className="stat-grid">
        <Stat value={people.patients} label="Patients on record" />
        <Stat value={people.doctors} label="Doctors" foot="Active accounts" />
        <Stat value={people.nurses} label="Nurses" foot="Active accounts" />
        <Stat value={people.deactivated} label="Deactivated"
          tone={people.deactivated > 0 ? 'warn' : undefined}
          foot="Kept — their name is on the record" />
      </div>

      <h2>Clinical activity</h2>
      <div className="stat-grid">
        <Stat value={activity.visits_total} label="Visits recorded"
          foot={`${activity.visits_completed} completed`} />
        <Stat value={activity.visits_open} label="Still open"
          tone={activity.visits_open > 0 ? 'warn' : 'ok'} foot="Not yet completed" />
        <Stat value={activity.visits_this_week} label="Visits this week"
          foot={`${activity.visits_today} today`} />
        <Stat value={activity.waiting_now} label="Waiting now"
          foot={`${activity.arrivals_total} arrivals all time`} />
      </div>

      <h2>The assistant</h2>
      <div className="stat-grid">
        <Stat value={assistant.assistant_runs} label="Assistant runs" tone="ai"
          foot="Differentials, codes, treatment" />
        <Stat value={assistant.reports_uploaded} label="Reports uploaded" tone="ai"
          foot={`${assistant.reports_analysed} analysed`} />
        <Stat value={assistant.reference_documents} label="Added references" tone="ai"
          foot="Beside the curated guidelines" />
        <Stat value={assistant.medication_warnings} label="Medication warnings" tone="ai"
          foot="Raised by the safety screen" />
      </div>

      <div className="dash-grid">
        <Panel title="Fourteen days of activity">
          <Trend
            days={stats.trend}
            series={[{ key: 'visits', label: 'Visits' }, { key: 'arrivals', label: 'Arrivals', alt: true }]}
          />
        </Panel>

        <Panel title="Where visits stand">
          <BarList
            rows={stats.visits_by_status}
            tone={(r) => (r.label === 'COMPLETED' ? 'ok' : 'warn')}
          />
        </Panel>

        <Panel title="Visits by doctor">
          <BarList rows={stats.visits_by_doctor}
            tone={(r) => (r.label === 'Unclaimed' ? 'bad' : undefined)} />
        </Panel>

        <Panel title="Queue by priority">
          <BarList rows={stats.priorities} tone={priorityTone} empty="Nobody is waiting." />
        </Panel>

        <Panel title="Most recorded diagnoses">
          <BarList rows={stats.top_diagnoses} empty="No diagnoses recorded yet." />
        </Panel>

        <Panel title="Most frequent audited actions">
          <BarList rows={stats.audit_actions} empty="Nothing audited yet." />
        </Panel>
      </div>

      <Panel title="Doctor workload" count={`${stats.staff_workload?.length ?? 0} doctors`}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Doctor</th>
                <th>Status</th>
                <th className="right">Visits</th>
                <th className="right">Open</th>
              </tr>
            </thead>
            <tbody>
              {stats.staff_workload?.map((row) => (
                <tr key={row.name}>
                  <td><strong>{row.name}</strong></td>
                  <td>
                    <span className={row.active ? 'pill ok' : 'pill bad'}>
                      {row.active ? 'active' : 'deactivated'}
                    </span>
                  </td>
                  <td className="right">{row.total_visits}</td>
                  <td className="right">{row.open_visits}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ helpers */

const firstName = (name = '') => name.replace(/^Dr\.?\s+/i, '').split(' ')[0]

function timeOf(value) {
  if (!value) return 'an unknown time'
  const when = new Date(value)
  const sameDay = when.toDateString() === new Date().toDateString()
  return sameDay
    ? when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : when.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
