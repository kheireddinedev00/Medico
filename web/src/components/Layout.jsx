import { useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { useTheme } from '../theme'
import ProfileMenu from './ProfileMenu'

/**
 * The shell every signed-in page sits in.
 *
 * A sidebar rather than a row of tabs, and every destination is in it at all times. The
 * previous version showed four tabs and hid the rest, which meant a doctor mid-consultation
 * had no way back to the reference library without going through the queue first. Navigation
 * that disappears depending on where you are is navigation people stop trusting.
 *
 * The two round buttons ride the sidebar's right edge, as in the Medico prototype: collapse
 * above, light-or-dark below. Putting them on the seam is what makes them read as belonging
 * to the panel they act on rather than floating in the page.
 *
 * Links are filtered by role rather than disabled, so nobody is shown a door that will
 * refuse them. The API enforces the same rules independently — this is convenience, never
 * the control.
 */
export default function Layout() {
  const { user, signOut } = useAuth()
  const { light, toggle } = useTheme()
  const { pathname } = useLocation()

  const [engine, setEngine] = useState(null)
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('medico.sidebar') === 'collapsed')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [waiting, setWaiting] = useState(null)

  /*
   * Whether the assistant is reachable, checked on arrival and then every two minutes.
   *
   * This was already once-per-mount, but the shell mounts once and stays — so the badge
   * could sit on a stale "offline" for an entire session after the engine came back up.
   * A poll fixes that and costs one request every two minutes.
   */
  useEffect(() => {
    const check = () => api.engineHealth().then(setEngine).catch(() => setEngine(null))
    check()
    const tick = setInterval(check, 120_000)
    return () => clearInterval(tick)
  }, [])

  /*
   * How many people are waiting, on the navigation itself.
   *
   * The one number worth interrupting someone for. A doctor reading a chart should not have
   * to open the queue to discover that a CRITICAL patient arrived while they were reading.
   */
  const refreshCount = useCallback(() => {
    if (user.role === 'patient') return
    api.waitingCount()
      .then((d) => setWaiting(d.waiting))
      .catch(() => setWaiting(null))
  }, [user.role])

  /*
   * Once on arrival, then on a timer — not on every navigation.
   *
   * Refetching per page change cost a request on every click to move a number that
   * changes when someone walks through the door, not when a doctor opens a chart. A
   * minute is well inside the time it takes to act on a new arrival.
   */
  useEffect(() => {
    refreshCount()
    const tick = setInterval(refreshCount, 60_000)
    return () => clearInterval(tick)
  }, [refreshCount])

  const toggleCollapse = () => {
    setCollapsed((was) => {
      localStorage.setItem('medico.sidebar', was ? 'open' : 'collapsed')
      return !was
    })
  }

  // Closed on navigation, or the drawer stays over the page it just moved to.
  useEffect(() => { setMobileOpen(false) }, [pathname])

  const links = [
    { to: '/dashboard', label: 'Dashboard', icon: '◧', roles: ['doctor', 'nurse', 'admin'] },
    { to: '/waiting-room', label: 'Waiting room', icon: '⏱', roles: ['nurse', 'doctor', 'admin'], count: waiting },
    { to: '/in-progress', label: 'In progress', icon: '◐', roles: ['doctor', 'admin'] },
    { to: '/patients', label: 'Patients', icon: '☰', roles: ['nurse', 'doctor', 'admin', 'patient'] },
    { to: '/references', label: 'References', icon: '❐', roles: ['doctor', 'nurse', 'admin'] },
    { to: '/staff', label: 'Staff', icon: '⚇', roles: ['admin'] },
  ].filter((l) => l.roles.includes(user.role))

  const page = pageFor(pathname)

  return (
    <div className={`shell${collapsed ? ' collapsed' : ''}`}>
      <div className="aurora" aria-hidden="true"><span /><span /><span /></div>

      <aside className={`sidebar${collapsed ? ' collapsed' : ''}${mobileOpen ? ' open' : ''}`}>
        {/* On the seam, half over each side, exactly as the prototype has them. */}
        <button
          className="rail-btn rail-collapse"
          onClick={toggleCollapse}
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '›' : '‹'}
        </button>

        <button
          className="rail-btn rail-theme"
          onClick={toggle}
          aria-label="Switch between light and dark"
          title={light ? 'Switch to dark' : 'Switch to light'}
        >
          <span>{light ? '☀' : '☾'}</span>
        </button>

        <div className="brand">
          <span className="brand-mark">🩺</span>
          <span className="brand-word">Medico</span>
        </div>

        <div className="nav-group">Clinic</div>

        <nav className="sidebar-nav">
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`}
            title={collapsed ? l.label : undefined}
          >
            <span className="nav-icon" aria-hidden="true">{l.icon}</span>
            <span className="nav-label">{l.label}</span>
            {/* Only when there is something to say. A badge reading "0" is noise. */}
            {l.count > 0 && <span className="nav-count">{l.count}</span>}
          </NavLink>
        ))}
        </nav>

        {/*
          Also in the profile menu, and the repetition is deliberate: this is where you
          look when you are leaving, that is where you look when you are thinking about
          your account.
        */}
        <div className="sidebar-foot">
          <button className="logout-btn" onClick={signOut} title={collapsed ? 'Sign out' : undefined}>
            <span className="nav-icon" aria-hidden="true">⏻</span>
            <span className="nav-label">Sign out</span>
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div>
            <div className="topbar-crumb">
              <button className="ghost no-print mobile-menu" onClick={() => setMobileOpen((v) => !v)}
                aria-label="Menu">☰</button>
              <span>Medico</span>
              <span className="sep" aria-hidden="true">›</span>
              <span className="where">{workspaceFor(user.role)}</span>
            </div>

            <h1>{page.title}</h1>
            <p className="topbar-lede">{page.lede}</p>
          </div>

          <div className="top-actions">
            {/* A doctor should learn the assistant is down here, not by wondering why the
                differential came back empty. */}
            <span
              className={engine ? 'pill ok' : 'pill bad'}
              title={engine ? `ICD-10: ${engine.icd10_mode}` : 'Start the engine on port 8001'}
            >
              {engine ? 'assistant ready' : 'assistant offline'}
            </span>

            <ProfileMenu />
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
    </div>
  )
}

/**
 * What this page is, said on the page.
 *
 * A name and one line about it. The prototype's bar carries the same thing, and it earns
 * its space: "In progress" means nothing until you know it holds visits parked on a
 * laboratory, and nobody reads documentation to find that out.
 */
function pageFor(pathname) {
  if (pathname.startsWith('/patients/')) {
    return { title: 'Patient chart', lede: 'History, visits and reports for one patient.' }
  }

  return {
    '/dashboard': {
      title: 'Dashboard',
      lede: 'What needs your attention, counted from the record as it stands.',
    },
    '/waiting-room': {
      title: 'Waiting room',
      lede: 'Who is here, how urgent they are, and which doctor they are waiting for.',
    },
    '/in-progress': {
      title: 'In progress',
      lede: 'Visits still open — including those parked waiting on a laboratory.',
    },
    '/patients': {
      title: 'Patients',
      lede: 'Everyone on record, filterable by age, history and how often they have been seen.',
    },
    '/references': {
      title: 'Reference library',
      lede: 'What the assistant is allowed to reason from, and what your clinic has added.',
    },
    '/staff': {
      title: 'Staff',
      lede: 'Clinic accounts. Doctors and nurses do not sign themselves up.',
    },
  }[pathname] ?? { title: 'Medico', lede: '' }
}

/** What this role calls the part of the clinic they work in. */
function workspaceFor(role) {
  return {
    doctor: 'Clinical workspace',
    nurse: 'Triage and intake',
    admin: 'Administration',
  }[role] ?? 'Clinic'
}
