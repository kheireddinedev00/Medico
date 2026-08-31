/**
 * Routes.
 *
 * Real URLs, so the back button works and a consultation can be linked to directly —
 * which matters more than it sounds when you are testing the same visit repeatedly.
 *
 * This is still a harness rather than the interface. `src/api.js` is the part built to
 * outlive it.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import WaitingRoomPage from './pages/WaitingRoomPage'
import PatientsPage from './pages/PatientsPage'
import PatientProfilePage from './pages/PatientProfilePage'
import InProgressPage from './pages/InProgressPage'
import ConsultationPage from './pages/ConsultationPage'
import StaffPage from './pages/StaffPage'
import ReferencesPage from './pages/ReferencesPage'

export default function App() {
  return (
    <AuthProvider>
      <Router />
    </AuthProvider>
  )
}

function Router() {
  const { user, loading } = useAuth()

  if (loading) return <p className="centered muted">Loading…</p>
  if (!user) return <LoginPage />

  // A patient account has one destination and no reason to see a patient list.
  const home = user.role === 'patient'
    ? `/patients/${user.patient_id}`
    : user.role === 'nurse' ? '/waiting-room' : '/patients'

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to={home} replace />} />
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
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>

      {/* The consultation runs full-width with its own sidebar, outside the shell. */}
      <Route path="/consultations/:visitId" element={<ConsultationPage />} />
    </Routes>
  )
}
