/**
 * Routes.
 *
 * Real URLs, so the back button works and a consultation can be linked to directly —
 * which matters more than it sounds when you are testing the same visit repeatedly.
 *
 * `/` is the public landing page whether or not anyone is signed in. A marketing page that
 * disappears once you have an account is a page you cannot show anyone.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import { ThemeProvider } from './theme'
import Layout from './components/Layout'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import WaitingRoomPage from './pages/WaitingRoomPage'
import PatientsPage from './pages/PatientsPage'
import PatientProfilePage from './pages/PatientProfilePage'
import InProgressPage from './pages/InProgressPage'
import ConsultationPage from './pages/ConsultationPage'
import StaffPage from './pages/StaffPage'
import StatisticsPage from './pages/StatisticsPage'
import ReferencesPage from './pages/ReferencesPage'

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Router />
      </AuthProvider>
    </ThemeProvider>
  )
}

function Router() {
  const { user, loading } = useAuth()

  if (loading) return <p className="centered muted">Loading…</p>

  // Signed out: the front door and the login form, and nothing else resolves.
  if (!user) {
    return (
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    )
  }

  // A patient account has one destination and no reason to see a patient list.
  const home = user.role === 'patient' ? `/patients/${user.patient_id}` : '/dashboard'

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<Navigate to={home} replace />} />

      <Route element={<Layout />}>
        <Route path="/dashboard" element={
          user.role === 'patient' ? <Navigate to={home} replace /> : <DashboardPage />
        } />
        <Route path="/waiting-room" element={<WaitingRoomPage />} />
        <Route path="/patients" element={<PatientsPage />} />
        <Route path="/patients/:patientId" element={<PatientProfilePage />} />
        <Route path="/in-progress" element={<InProgressPage />} />
        <Route path="/references" element={<ReferencesPage />} />
        {/*
          Typed URLs reach a route the nav bar does not show. The API refuses regardless —
          this only decides whether a nurse who guesses the address gets a 403 rendered as
          a page, or is simply sent back where they belong.
        */}
        <Route
          path="/staff"
          element={user.role === 'admin' ? <StaffPage /> : <Navigate to={home} replace />}
        />
        <Route
          path="/statistics"
          element={user.role === 'admin' ? <StatisticsPage /> : <Navigate to={home} replace />}
        />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>

      {/* The consultation runs full-width with its own sidebar, outside the shell. */}
      <Route path="/consultations/:visitId" element={<ConsultationPage />} />
    </Routes>
  )
}
