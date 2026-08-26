# Respiratory Clinical Decision Support Assistant

An AI assistant that helps a **physician** reason through a respiratory consultation. It
suggests a differential diagnosis, ICD-10 codes, investigations and treatment, explains
its reasoning, and cites the reference material it was given.

It does not diagnose. The physician decides, and the record stores what the assistant
suggested and what the physician chose, separately.

## The three safety properties

These are enforced by code and covered by tests, not requested in a prompt:

1. **The assistant cannot write to the record.** `chatbot.consultation.assess()` takes no
   repository. Every write happens in `clinical/session.py`, in response to a method the
   physician's interface called.
2. **A drug the patient is allergic to is never offered.** Everything the model suggests
   is screened in `clinical/medications.py` against recorded allergies, current
   medications and chronic conditions. Allergy-class matches are withheld and shown with
   the reason; interactions are flagged.
3. **Nothing is claimed with false precision.** A likelihood expressed as a percentage is
   rejected outright, and a citation pointing at a source that was not retrieved is
   dropped before the physician sees it.

## Running it

Everything runs from the project root with the virtual environment's Python.

```bash
venv/Scripts/python -m storage.cli seed --reset
```

Loads the fictional test patients from `data/seed/patients.json`.

```bash
venv/Scripts/python -m chatbot.cli --patient P-001
```

Starts a consultation. Add `--findings data/seed/findings_example.json` to skip typing,
`--show-context` to see exactly what the model is told.

```bash
venv/Scripts/python -m chatbot.cli --resume V-002
```

Continues a visit that was left waiting for test results.

```bash
venv/Scripts/python -m soap.cli V-002
```

Writes the SOAP note for a visit. See [SOAP notes](#soap-notes) below.

### Everything else

| Command | What it does |
|---|---|
| `python -m storage.cli patients` | List patients |
| `python -m storage.cli show P-001` | One patient's stored record |
| `python -m storage.cli timeline P-001` | Visits and reports in date order |
| `python -m storage.cli context P-001` | The exact text sent to the model |
| `python -m chatbot.cli --patient P-001 --visits` | That patient's visits |
| `python -m report_reader.cli extract <file>` | Report to JSON, nothing stored |
| `python -m report_reader.cli upload <file> --patient P-001 --visit V-002` | Store a report |
| `python -m report_reader.cli analyze <report-id>` | Analyse a stored report |
| `python -m report_reader.cli list --patient P-001` | That patient's reports |
| `python -m soap.cli --patient P-001 --list` | That patient's visits, with their ids |
| `python -m soap.cli --patient P-001 --all` | A note for every visit, oldest first |
| `python -m rag.ingest --dry-run` | What would be ingested, without touching the store |
| `python -m rag.ingest` | Rebuild the vector store |
| `python -m pytest -q` | The test suite |

## How a consultation flows

```
INITIAL_ASSESSMENT   findings entered, differential suggested, physician picks a diagnosis
        ↓
ICD10_SELECTION      codes suggested, physician picks one (optional)
        ├─ Option A ─→ TEST_SELECTION → WAITING_FOR_TESTS
        │                                      ↓  results uploaded and analysed
        │                                 RESULTS_REVIEW  ← re-assessed with the results
        │                                      ↓
        └─ Option B ──────────────────→ TREATMENT_SELECTION
                                               ↓
                                        FOLLOW_UP / COMPLETED
```

Transitions are enforced in `clinical/consultation_state.py`. A visit cannot reach a
closed state without a diagnosis having been chosen.

## SOAP notes

Any visit can be written up as a SOAP note, at any stage, at any time:

```bash
venv/Scripts/python -m soap.cli V-002 --format markdown --out note.md
```

`--patient P-001` uses that patient's latest visit when you don't have the visit id,
`--all` writes one note per visit, and `--format json` returns the note as structured
data for a web interface. `--no-ai` leaves the assistant's differential out.

Three things are true of every note:

- **No model is involved.** The note is the record rearranged under four headings.
  Every line traces to a stored field, so there is nothing in it to fact-check.
- **The assistant stays labelled.** Assessment holds the physician's working diagnosis.
  What the assistant proposed appears below it under its own heading, never merged in.
- **Nothing is stored.** Notes are rebuilt from the chart on request, so a note cannot
  go stale against the record. A note for a past visit shows only what was known on
  that day — a diagnosis reached later does not appear in its background.

Generating a note is read-only and never touches the record.

## The HTTP service

The same engine, behind a REST API, for a web backend to call:

```bash
venv/Scripts/python -m uvicorn service.app:app --reload --port 8001
```

Interactive documentation at `http://127.0.0.1:8001/docs`.

Two properties define it, and they are the mirror image of each other:

- **It stores nothing.** There is no database behind the service process. Each request
  carries the chart — profile, visit, history, reports — and each response carries what
  the engine produced. The caller owns the record and does all the writing.
- **It decides nothing that the engine does not already decide.** Every route delegates to
  a function that already existed. The backend does not evaluate transitions, screen drugs
  or validate codes; it asks and stores the answer. Clinical logic implemented twice, in
  two languages, is clinical logic that will eventually disagree with itself.

Because the service does the writing nowhere, a transition response carries a `persist`
flag: the engine's own answer to whether this visit belongs in the record yet. It is false
until a diagnosis is chosen, so a consultation the physician abandons leaves nothing
behind — the same rule the CLI follows, travelling over HTTP rather than being restated by
the caller.

`GET /reference/workflow` serves the state machine so a front end can grey out what the
engine would refuse, rather than keeping its own copy of the transition table to drift
against.

Set `SERVICE_API_KEY` in `.env` and send it as `X-Service-Key`. The service answers
clinical questions about named patients: bind it to localhost, and do not expose the port.

## Adding your own material

### Reference documents for the assistant

1. Put the PDF in `data/references/`.
2. Add an entry to `data/references/sources.json` with its citation and the page ranges
   that contain clinical guidance. **A file not declared there is ignored** — this is
   deliberate, so the corpus stays curated and every chunk keeps its provenance.
3. `python -m rag.ingest --dry-run` to check what would be kept, then
   `python -m rag.ingest` to rebuild.

### ICD-10 codes

`data/icd10_respiratory.json` holds 299 codes: the WHO 2019 export of chapter J
(J00–J99), plus 19 codes from chapters A, C, I, R and U — tuberculosis, COVID-19, lung
cancer, pulmonary embolism, heart failure and the respiratory symptom codes — which a
J00–J99 export does not contain but the assistant regularly needs.

Because the list is populated, the permitted codes are injected into the prompt, anything
not on the list is dropped, and the description shown to the physician comes from this
file rather than from the model.

To extend it, add entries of the form:

```json
{ "code": "J44.1", "description": "...", "synonyms": ["COPD exacerbation"] }
```

`synonyms` is optional and only helps matching — put words a doctor would type that are
not in the official description. Emptying the `codes` array reverts the assistant to
checking code *format* only, and the CLI says which mode it is in.

### Test patients

Edit `data/seed/patients.json` and re-run `python -m storage.cli seed --reset`. The file
is the master copy; the database is a loaded copy of it.

## Architecture

```
chatbot/          talking to the model — prompt, one assessment call, the CLI
clinical/         clinical reasoning — differential, ICD-10, investigations,
                  medications + safety screen, consultation state machine, session
patient/          the record — profile, visit, report, and the context builder
rag/              the reference library — curation, chunking, embedding, retrieval
report_reader/    reports — extraction (transcription only) and analysis (separate)
soap/             SOAP notes — a rendering of the record, no reasoning, standalone
storage/          SQLite persistence behind repository protocols
service/          the engine over HTTP — stateless, stores nothing, adds no logic
```

`soap/` depends on the patient models and nothing depends on it, so it can be removed
without affecting the consultation flow.

Two boundaries are load-bearing:

**`storage/repository.py` is a seam.** The clinical layer depends only on those
protocols, so replacing SQLite with a web backend means writing one adapter and changing
nothing else.

**Extraction and analysis are separate.** `report_reader/extractor.py` transcribes and
interprets nothing, so the stored JSON stays a faithful record of what was printed.
`report_reader/analyzer.py` interprets, only when the physician asks it to. Within the
analyser, deciding a value is out of range is arithmetic done in code — the model only
describes what the flags show.

## Configuration

Copy `.env.example` to `.env` and add an OpenRouter API key. Defaults use a free model.

| Setting | Default | Meaning |
|---|---|---|
| `OPENROUTER_MODEL` | `google/gemma-4-31b-it:free` | The reasoning model |
| `VLM_MODEL` | `google/gemma-4-31b-it:free` | The report-reading model |
| `CONTEXT_RECENT_VISITS` | 3 | Past visits summarised into the prompt |
| `CONTEXT_RECENT_REPORTS` | 4 | Past reports summarised into the prompt |
| `EVIDENCE_CHUNKS` | 6 | Reference excerpts retrieved per assessment |

## Known limits

- **ICD-10 coding rules are not implemented.** Even with the curated list, choosing
  J44.0 over J44.1 is a clinical judgement, not a lookup.
- **The interaction tables in `clinical/medications.py` are small and hand-written.** They
  cover respiratory primary care. A deployment would replace them with a licensed
  interaction database.
- **The corpus is uneven.** It holds five sources — the GOLD 2026 COPD report, the GINA
  2026 asthma summary, NICE NG250 on pneumonia, the WHO COVID-19 living guideline, and the
  WHO PAL manual — covering COPD, asthma, pneumonia and COVID-19 well. Tuberculosis and
  lung cancer are moderate. **Pharyngitis, sinusitis and allergic rhinitis are close to
  absent**, so the assistant will suggest them from clinical findings with no citation.
- **No authentication, no audit of who did what.** Single-user prototype.
- **Records cannot be corrected.** Notes are append-only and a completed visit cannot be
  reopened; a mistake means a new visit.

## Disclaimer

Decision support for qualified clinicians. Not a medical device, not validated for
clinical use, and not a substitute for professional judgement. The test patients are
fictional and contain no real patient data.
