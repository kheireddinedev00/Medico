import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import Modal from '../components/Modal'

/**
 * Clinic accounts.
 *
 * The administrator's screen. Clinicians do not sign themselves up — an account that can
 * prescribe, or that can enter the allergy list a prescription is screened against, is
 * granted deliberately, and an audit trail is only worth reading when every account belongs
 * to a known person.
 *
 * Accounts are deactivated rather than deleted, and the screen says so. Their name is on
 * visits, prescriptions, uploaded reports and audit rows; removing the row would either take
 * that history with it or leave it pointing at nobody.
 */
export default function StaffPage() {
  const [staff, setStaff] = useState(null)
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)

  const refresh = useCallback(() => {
    api.staff().then((d) => setStaff(d.staff)).catch(setError)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const act = async (fn) => {
    setError(null)
    try { await fn(); refresh() } catch (e) { setError(e) }
  }

  // Before the loading branch, not after it. A refusal that leaves "Loading…" on screen
  // forever reads as a broken page rather than a closed door.
  if (!staff) {
    return error
      ? <div className="page"><div className="note bad">{error.message}</div></div>
      : <p className="empty">Loading accounts…</p>
  }

  const byRole = [
    ['doctor', 'Doctors'],
    ['nurse', 'Nurses'],
    ['admin', 'Administrators'],
  ]

  return (
    <div className="page">
      <div className="page-head">
        <h1>Staff</h1>
        <button className="primary" onClick={() => setModal({ kind: 'create' })}>
          New account
        </button>
      </div>

      {error && <div className="note bad">{error.message}</div>}

      {byRole.map(([role, heading]) => {
        const rows = staff.filter((u) => u.role === role)
        if (rows.length === 0) return null

        return (
          <div key={role} className="card">
            <div className="card-head">
              <h2>{heading}</h2>
              <span className="muted small">{rows.length}</span>
            </div>

            <table>
              <thead>
                <tr><th>Name</th><th>Email</th><th>Status</th><th className="right">Actions</th></tr>
              </thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.name}</strong>
                      {u.is_you && <span className="pill info"> you</span>}
                    </td>
                    <td className="muted">{u.email}</td>
                    <td>
                      <span className={u.is_active ? 'pill ok' : 'pill bad'}>
                        {u.is_active ? 'active' : 'deactivated'}
                      </span>
                    </td>
                    <td className="right">
                      <button onClick={() => setModal({ kind: 'edit', user: u })}>Edit</button>
                      <button onClick={() => setModal({ kind: 'password', user: u })}>
                        Set password
                      </button>
                      {u.is_active
                        ? (
                          <button className="danger" disabled={u.is_you}
                            title={u.is_you ? 'You cannot deactivate your own account' : undefined}
                            onClick={() => setModal({ kind: 'deactivate', user: u })}>
                            Deactivate
                          </button>
                        )
                        : (
                          <button onClick={() => act(() => api.updateStaff(u.id, { is_active: true }))}>
                            Reactivate
                          </button>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}

      {modal?.kind === 'create' && (
        <Modal title="New staff account" onClose={() => setModal(null)}>
          <AccountForm
            onCancel={() => setModal(null)}
            onSubmit={async (values) => { await api.createStaff(values); setModal(null); refresh() }}
          />
        </Modal>
      )}

      {modal?.kind === 'edit' && (
        <Modal title={`Edit — ${modal.user.name}`} onClose={() => setModal(null)}>
          <AccountForm
            user={modal.user}
            onCancel={() => setModal(null)}
            onSubmit={async (values) => {
              await api.updateStaff(modal.user.id, values); setModal(null); refresh()
            }}
          />
        </Modal>
      )}

      {modal?.kind === 'password' && (
        <Modal title={`Set a password — ${modal.user.name}`} onClose={() => setModal(null)}>
          <PasswordForm
            onCancel={() => setModal(null)}
            onSubmit={async (password) => {
              await api.resetStaffPassword(modal.user.id, password); setModal(null)
            }}
          />
        </Modal>
      )}

      {modal?.kind === 'deactivate' && (
        <Modal title="Deactivate this account?" onClose={() => setModal(null)}>
          <p><strong>{modal.user.name}</strong> will not be able to sign in, and any session
            they have open is ended.</p>
          <p className="muted small">
            The account is kept, not deleted. Their name stays on the visits, prescriptions
            and notes they recorded — a record that forgets who made an entry is worse than
            one with a disabled account in it.
          </p>
          <div className="form-actions">
            <button className="danger" onClick={() => {
              act(() => api.deactivateStaff(modal.user.id)); setModal(null)
            }}>Deactivate</button>
            <button onClick={() => setModal(null)}>Keep active</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function AccountForm({ user, onSubmit, onCancel }) {
  const editing = !!user
  const [values, setValues] = useState({
    name: user?.name ?? '',
    email: user?.email ?? '',
    role: user?.role ?? 'doctor',
    password: '',
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const set = (k) => (e) => setValues({ ...values, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const payload = editing
        ? { name: values.name, email: values.email, role: values.role }
        : values
      await onSubmit(payload)
    } catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally { setBusy(false) }
  }

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}

      <label>Full name
        <input value={values.name} onChange={set('name')} autoFocus placeholder="Dr. Amina Belkacem" />
      </label>
      <label>Email
        <input type="email" value={values.email} onChange={set('email')} placeholder="name@clinic.test" />
      </label>
      <label>Role
        <select value={values.role} onChange={set('role')}>
          <option value="doctor">Doctor</option>
          <option value="nurse">Nurse</option>
          <option value="admin">Administrator</option>
        </select>
      </label>

      {!editing && (
        <>
          <label>Initial password
            <input type="password" value={values.password} onChange={set('password')}
              placeholder="at least 8 characters" />
          </label>
          <p className="muted small">
            Give it to them directly and have them change it. This screen can set a new one
            at any time.
          </p>
        </>
      )}

      <div className="form-actions">
        <button className="primary" disabled={busy || !values.name || !values.email}>
          {busy ? 'Saving…' : editing ? 'Save changes' : 'Create account'}
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function PasswordForm({ onSubmit, onCancel }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try { await onSubmit(password); setDone(true) }
    catch (err) {
      setError(err.body?.errors ? Object.values(err.body.errors)[0][0] : err.message)
    } finally { setBusy(false) }
  }

  if (done) return <div className="note ok">Password changed, and their open sessions ended.</div>

  return (
    <form className="form" onSubmit={submit}>
      {error && <div className="note bad">{error}</div>}
      <label>New password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoFocus placeholder="at least 8 characters" />
      </label>
      <p className="muted small">
        Any session that account has open is signed out — a reset that leaves the old
        sessions alive has not really taken the account back.
      </p>
      <div className="form-actions">
        <button className="primary" disabled={busy || password.length < 8}>
          {busy ? 'Saving…' : 'Set password'}
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}
