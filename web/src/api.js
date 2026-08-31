/**
 * The API client, for whichever front end ends up using it.
 *
 * This file is the part of `web/` worth keeping when the real interface arrives. Everything
 * else here is a harness; this is the shape of the contract.
 *
 * Two things about the API that a client has to get right:
 *
 * 1. **`persisted`.** Consultation responses carry it, and it is not a loading state. False
 *    means the physician has not committed to a diagnosis and the visit is not in the
 *    record — a doctor who closes the tab now leaves nothing behind. Show that honestly
 *    rather than pretending the consultation is saved.
 *
 * 2. **409 is not an error to hide.** It means the workflow does not allow that step, and
 *    the message is written for a clinician. Show it to them verbatim.
 */

const TOKEN_KEY = 'cdss.token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Request failed (${status})`)
    this.status = status
    this.body = body
    // 409 = the engine refused on clinical grounds. 503 = the assistant is unavailable,
    // which must never be rendered as an empty answer.
    this.isRefusal = status === 409
    this.isEngineDown = status === 503
  }
}

async function request(method, path, body) {
  const headers = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let payload
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const response = await fetch(`/api${path}`, { method, headers, body: payload })
  const text = await response.text()
  const json = text ? JSON.parse(text) : null

  if (!response.ok) throw new ApiError(response.status, json)
  return json
}

const get = (path) => request('GET', path)
const post = (path, body) => request('POST', path, body)
const del = (path) => request('DELETE', path)

export const api = {
  // --- auth ---
  login: (email, password) => post('/login', { email, password }),
  logout: () => post('/logout'),
  me: () => get('/me'),
  // Your own name, title and photo. Never your role — that is an administrator's.
  updateProfile: (body) => request('PATCH', '/me', body),

  // --- reference ---
  // The transition table comes from the engine. A client that hard-codes it will disagree
  // with the server eventually, and a doctor will find the disagreement.
  workflow: () => get('/workflow'),
  // The curated 299-code list, searchable. A physician can code a diagnosis the assistant
  // did not suggest, but still cannot invent a code.
  searchIcd10: (search = '') => get(`/icd10?search=${encodeURIComponent(search)}`),
  engineHealth: () => get('/engine/health'),

  // --- record ---
  patients: (search = '') => get(`/patients?search=${encodeURIComponent(search)}`),
  patient: (id) => get(`/patients/${id}`),
  timeline: (id) => get(`/patients/${id}/timeline`),
  // Scoped to the visit: vitals belong to an attendance, so a patient seen last week
  // must not have those readings offered as though they were today's.
  triageVitals: (id, visitId) =>
    get(`/patients/${id}/triage-vitals${visitId ? `?visit_id=${visitId}` : ''}`),

  // --- intake ---
  // The only way allergies, medications and conditions ever enter the system. An allergy
  // missed here is one the safety screen will never get to withhold a drug for.
  createPatient: (body) => post('/patients', body),
  updatePatient: (id, body) => request('PATCH', `/patients/${id}`, body),
  addAllergy: (id, body) => post(`/patients/${id}/allergies`, body),
  removeAllergy: (id, allergyId) => del(`/patients/${id}/allergies/${allergyId}`),
  addMedication: (id, body) => post(`/patients/${id}/medications`, body),
  stopMedication: (id, medId) => post(`/patients/${id}/medications/${medId}/stop`),
  removeMedication: (id, medId) => del(`/patients/${id}/medications/${medId}`),
  addCondition: (id, body) => post(`/patients/${id}/conditions`, body),
  removeCondition: (id, condId) => del(`/patients/${id}/conditions/${condId}`),

  // --- waiting room ---
  // 'active' = waiting or with the doctor. Someone being seen has not left the room.
  // order: 'priority' (sickest first) or 'arrival' (purely who came first). The sort is
  // done by the API, not here — the ordering rule belongs with the ranks it sorts on.
  queue: (status = 'active', order = 'priority') =>
    get(`/waiting-room?status=${status}&order=${order}`),
  // visitId null means a new problem; set means they are back about that open visit.
  arrive: (patientId, visitId = null, doctorId = null) =>
    post('/waiting-room', { patient_id: patientId, visit_id: visitId, doctor_id: doctorId }),
  setQueueVisit: (entryId, visitId) => post(`/waiting-room/${entryId}/visit`, { visit_id: visitId }),
  resumableVisits: (patientId) => get(`/patients/${patientId}/resumable-visits`),
  // Saving vitals also triages. One call, because a nurse who has to press a second
  // button to score someone will eventually not press it.
  recordVitals: (entryId, vitals) => post(`/waiting-room/${entryId}/vitals`, vitals),
  markSeen: (entryId, visitId) => post(`/waiting-room/${entryId}/seen`, { visit_id: visitId }),
  removeFromQueue: (entryId) => del(`/waiting-room/${entryId}`),

  // Which doctor a waiting patient is for. Null unassigns, which is a real state:
  // the patient stays in the shared queue where the nurses can resolve it.
  assignDoctor: (entryId, doctorId) =>
    post(`/waiting-room/${entryId}/doctor`, { doctor_id: doctorId }),
  // Active doctors, for the assignment control.
  doctors: () => get('/doctors'),

  // --- staff accounts (administrator only) ---
  staff: () => get('/staff'),
  createStaff: (body) => post('/staff', body),
  updateStaff: (id, body) => request('PATCH', `/staff/${id}`, body),
  resetStaffPassword: (id, password) => post(`/staff/${id}/password`, { password }),
  // Deactivates. Their name stays on everything they recorded.
  deactivateStaff: (id) => del(`/staff/${id}`),

  // --- triage ---
  // Re-score without re-entering observations: the engine was down at the time, or the
  // readings have gone stale. It cannot find deterioration — only new measurements can.
  retriage: (entryId) => post(`/waiting-room/${entryId}/retriage`),
  // The clinician disagreeing with the agent. A reason is required by the API, not just
  // by the form, so an override always has something to read next to it later.
  setPriority: (entryId, priority, reason) =>
    post(`/waiting-room/${entryId}/priority`, { priority, reason }),
  clearPriority: (entryId) => del(`/waiting-room/${entryId}/priority`),
  // The ladder, the actions and the red-flag list, from the engine. Fetched rather than
  // written here so the UI cannot explain a priority the engine did not decide.
  triageRules: () => get('/triage/rules'),

  // --- consultation ---
  openConsultation: (patientId) => post('/consultations', { patient_id: patientId }),
  // Visits still open. This is how a consultation left at WAITING_FOR_TESTS is found again
  // when the results come back, days later.
  openVisits: () => get('/consultations/open'),
  resumeConsultation: (visitId) => get(`/consultations/${visitId}`),
  deleteVisit: (visitId) => del(`/consultations/${visitId}`),
  // The patient can leave the room. The visit stays open and resumable.
  leaveConsultation: (visitId) => post(`/consultations/${visitId}/leave`),
  nextStates: (visitId) => get(`/consultations/${visitId}/next-states`),
  // The generated draft, rebuilt from the record every time it is asked for.
  soap: (visitId) => get(`/consultations/${visitId}/soap`),
  // The version a physician reviewed and signed. Null until they save one.
  savedSoap: (visitId) => get(`/consultations/${visitId}/soap/saved`),
  saveSoap: (visitId, body) => post(`/consultations/${visitId}/soap/save`, body),
  // Empties the consultation. An erase, not a transition — see the engine's /reset.
  resetConsultation: (visitId) => post(`/consultations/${visitId}/reset`),
  findings: (visitId, findings) => post(`/consultations/${visitId}/findings`, findings),

  // The assistant. Suggestions only — none of these writes a decision.
  assess: (visitId) => post(`/consultations/${visitId}/assess`),
  suggestCodes: (visitId) => post(`/consultations/${visitId}/suggest/codes`),
  suggestInvestigations: (visitId) => post(`/consultations/${visitId}/suggest/investigations`),
  suggestMedications: (visitId) => post(`/consultations/${visitId}/suggest/medications`),
  // Runs the same safety screen over drugs the doctor typed. Reports; never refuses.
  checkMedications: (visitId, names) =>
    post(`/consultations/${visitId}/check-medications`, { names }),

  // The physician. Every one of these is a decision a human made.
  selectDiagnosis: (visitId, body) => post(`/consultations/${visitId}/diagnosis`, body),
  setCode: (visitId, code) => post(`/consultations/${visitId}/diagnosis/code`, { code }),
  reviseDiagnosis: (visitId, body) => post(`/consultations/${visitId}/diagnosis/revise`, body),
  investigate: (visitId) => post(`/consultations/${visitId}/investigate`),
  orderInvestigations: (visitId, investigations) =>
    post(`/consultations/${visitId}/investigations`, { investigations }),
  skipInvestigations: (visitId) => post(`/consultations/${visitId}/investigations/skip`),
  orderMore: (visitId) => post(`/consultations/${visitId}/investigations/more`),
  recordResults: (visitId, summary, resulted) =>
    post(`/consultations/${visitId}/results`, { summary, resulted }),
  // Replaces the recorded results text. Recording appends; this corrects.
  amendResults: (visitId, summary) =>
    post(`/consultations/${visitId}/results/amend`, { summary }),
  resultsFromReports: (visitId, reportIds) =>
    post(`/consultations/${visitId}/results/from-reports`, { report_ids: reportIds }),
  treat: (visitId) => post(`/consultations/${visitId}/treat`),
  prescribe: (visitId, medications) => post(`/consultations/${visitId}/prescribe`, { medications }),
  followUp: (visitId, plan) => post(`/consultations/${visitId}/follow-up`, { plan }),
  complete: (visitId) => post(`/consultations/${visitId}/complete`),
  addNote: (visitId, note) => post(`/consultations/${visitId}/note`, { note }),

  // --- reports ---
  reports: (patientId) => get(`/patients/${patientId}/reports`),
  report: (reportId) => get(`/reports/${reportId}`),
  uploadReport: (patientId, file, visitId, investigationId = null) => {
    const form = new FormData()
    form.append('file', file)
    if (visitId) form.append('visit_id', visitId)
    // Filing the upload against the test it answers is what makes the outstanding list
    // real — otherwise a visit waiting on three tests with two PDFs cannot say which two.
    if (investigationId) form.append('investigation_id', investigationId)
    return request('POST', `/patients/${patientId}/reports`, form)
  },
  // Deliberately separate from upload. Transcribing and interpreting are different acts.
  analyseReport: (reportId) => post(`/reports/${reportId}/analyse`),

  // Dashboard figures. One route, scoped to the caller's role inside the controller.
  stats: () => get('/stats'),

  // --- the assistant's reference library ---
  // The curated guidelines come back marked `removable: false`. That flag is a hint for
  // rendering, never the control: the API has no route that could remove them.
  references: () => get('/references'),
  addReference: (file, meta) => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', meta.title)
    // Omitted rather than sent empty — a blank year is not a year, and the engine parses
    // this field as an integer.
    for (const key of ['publisher', 'year', 'reference']) {
      if (meta[key]) form.append(key, meta[key])
    }
    return request('POST', '/references', form)
  },
  removeReference: (id) => del(`/references/${id}`),
}
