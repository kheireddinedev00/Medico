import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'
import { useEffect, useState } from 'react'
import { api } from '../api'

/**
 * The shell every page sits in.
 *
 * Navigation is filtered by role rather than merely disabled, so a nurse is not shown
 * doors that will refuse them. The API enforces the same rules independently — this is
 * convenience, never the control.
 */
export default function Layout() {
  const { user, signOut } = useAuth()
  const [engine, setEngine] = useState(null)

  useEffect(() => {
    api.engineHealth().then(setEngine).catch(() => setEngine(null))
  }, [])

  const links = [
    { to: '/waiting-room', label: 'Waiting room', roles: ['nurse', 'doctor', 'admin'] },
    { to: '/patients', label: 'Patients', roles: ['nurse', 'doctor', 'admin', 'patient'] },
    { to: '/in-progress', label: 'In progress', roles: ['doctor', 'admin'] },
    { to: '/staff', label: 'Staff', roles: ['admin'] },
  ].filter((l) => l.roles.includes(user.role))

  return (
    <div className="shell">
      <header className="topbar">
        <div className="row">
          <span className="brand">Respiratory CDSS</span>
          <nav className="tabs">
            {links.map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => isActive ? 'on' : ''}>
                {l.label}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="row">
          {/* A doctor should learn the assistant is down here, not by wondering why the
              differential came back empty. */}
          <span className={engine ? 'pill ok' : 'pill bad'} title={engine ? `ICD-10: ${engine.icd10_mode}` : 'Start the engine on port 8001'}>
            {engine ? 'assistant ready' : 'assistant offline'}
          </span>
          <span className="muted small">{user.name} · {user.role}</span>
          <button onClick={signOut}>Sign out</button>
        </div>
      </header>

      <main className="content">
        <Outlet />
      </main>

      <footer className="disclaimer">
        Decision support for qualified clinicians. Not a medical device. The physician is
        responsible for every clinical decision.
      </footer>
    </div>
  )
}
