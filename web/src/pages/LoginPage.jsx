import { useState } from 'react'
import { useAuth } from '../auth'

export default function LoginPage() {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('doctor@clinic.test')
  const [password, setPassword] = useState('password')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(email, password)
    } catch (err) {
      setError(err.body?.errors?.email?.[0] || err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="centered">
      <form className="card login" onSubmit={submit}>
        <h1>Respiratory CDSS</h1>
        <p className="muted small">
          Decision support for qualified clinicians. Not a medical device.
        </p>

        <label>Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
        </label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password" />
        </label>

        {error && <div className="note bad">{error}</div>}

        <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>

        {/* Seeded accounts, listed because this is a local harness. Not a habit to copy. */}
        <div className="accounts">
          {['doctor', 'nurse', 'admin'].map((role) => (
            <button key={role} type="button" className="link"
              onClick={() => { setEmail(`${role}@clinic.test`); setPassword('password') }}>
              {role}
            </button>
          ))}
        </div>
      </form>
    </div>
  )
}
