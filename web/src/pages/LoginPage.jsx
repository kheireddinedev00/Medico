import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth'

/**
 * Signing in.
 *
 * Stands on its own rather than inside the application shell — there is no navigation to
 * offer someone who is not signed in yet, and a sidebar full of doors they cannot open is
 * worse than none.
 */
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
      <div className="aurora" aria-hidden="true"><span /><span /><span /></div>

      {/*
        Drifting clinical glyphs behind the card, from the prototype's landing page.
        Decoration, and marked as such: they carry nothing, so a screen reader is told to
        skip them and `prefers-reduced-motion` stops them dead.
      */}
      <div className="floaters" aria-hidden="true">
        {['🫁', '🩺', '💊', '🧬', '🩻', '🌡', '❤', '🔬'].map((glyph, i) => (
          <span key={i} className={`floater f${i + 1}`}>{glyph}</span>
        ))}
      </div>

      <form className="card login" onSubmit={submit}>
        <div className="brand" style={{ padding: 0, marginBottom: 14 }}>
          <span className="brand-mark">🩺</span>
          <span>Medico</span>
        </div>

        <h1>Sign in</h1>
        <p className="muted small" style={{ marginTop: 4, marginBottom: 18 }}>
          Decision support for qualified clinicians. Not a medical device.
        </p>

        <div className="form">
          <label>Email
            <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
          </label>
          <label>Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" />
          </label>

          {error && <div className="note bad">{error}</div>}

          <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </div>

        {/* Seeded accounts, listed because this is a local harness. Not a habit to copy. */}
        <p className="muted small" style={{ marginBottom: 4, marginTop: 18 }}>Demo accounts</p>
        <div className="accounts">
          {['doctor', 'nurse', 'admin'].map((role) => (
            <button key={role} type="button" className="link"
              onClick={() => { setEmail(`${role}@clinic.test`); setPassword('password') }}>
              {role}
            </button>
          ))}
        </div>

        <p className="small" style={{ marginTop: 18, marginBottom: 0 }}>
          <Link to="/">← Back to the home page</Link>
        </p>
      </form>
    </div>
  )
}
