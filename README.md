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

Three processes, each with its own environment file. Copy the three `.env.example` files
before starting anything.

| service | folder | its env | what it needs to be told |
|---|---|---|---|
| engine — Python, FastAPI, port 8001 | project root | `.env` | model keys, data paths |
| API — Laravel, port 8000 | `api/` | `api/.env` | the database, and `ENGINE_URL` |
| front end — React, Vite, port 5173 | `web/` | `web/.env` | `VITE_API_URL` |

No service holds another's address in its code. The API finds the engine through
`ENGINE_URL`, the front end finds the API through `VITE_API_URL`, and moving either is a
change to an env file rather than a change to a source file. Both have working defaults for
the local setup below, so a missing `.env` does not stop you.

### The database

The Laravel API and the React front end need MySQL. The engine does not — it stores nothing
between requests; the chart arrives with each call and leaves with the response.

Start MySQL (XAMPP's control panel, or `C:\xampp\mysql\bin\mysqld.exe`), then create the
database once:

```sql
CREATE DATABASE respiratory_cdss CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE respiratory_cdss_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cdss'@'127.0.0.1' IDENTIFIED BY 'your-password';
GRANT ALL PRIVILEGES ON respiratory_cdss.* TO 'cdss'@'127.0.0.1';
GRANT ALL PRIVILEGES ON respiratory_cdss_test.* TO 'cdss'@'127.0.0.1';
```

The second database is for the test suite, so a test run never touches the clinic's data.
Copy `api/.env.example` to `api/.env`, set `DB_PASSWORD`, then:

```bash
cd api && php artisan migrate --seed
```

### Serve the API through Apache, not `artisan serve`

`php artisan serve` handles **one request at a time**. An AI assessment takes about twenty
seconds, and while it runs every other call queues behind it — the waiting-room count, the
engine badge, the page the doctor tried to open. The application appears frozen for work it
is not doing.

XAMPP's Apache serves them in parallel. Measured here, 160 requests eight at a time: 6.98s
through `artisan serve`, 1.93s through Apache.

`PHP_CLI_SERVER_WORKERS` does not help — it is fork-based and POSIX-only, so on Windows it
does nothing at all.

The virtual host lives in `C:\xampp\apache\conf\extra\httpd-vhosts.conf`:

```apache
Listen 8000

<VirtualHost *:8000>
    DocumentRoot "C:/Users/HP/Desktop/medical-ai-assistant/api/public"
    <Directory "C:/Users/HP/Desktop/medical-ai-assistant/api/public">
        AllowOverride All
        Require local
        Options -Indexes +FollowSymLinks
    </Directory>
</VirtualHost>
```

Port 8000 on purpose: it is what the front end already proxies to, so nothing else changes.
Start it with `C:\xampp\apache\bin\httpd.exe` or XAMPP's control panel, and do not run
`php artisan serve` at the same time — they want the same port and they are alternatives.

**The configuration must be cached, and this is not optional here.**

```bash
cd api && php artisan config:cache
```

Apache on Windows runs PHP as threads inside one process, on a thread-safe build. Laravel
parses `.env` on every request, and racing that parse across threads makes a request lose
its environment and fall back to framework defaults — it then looks for a database called
`laravel` and returns a 500. It is rare and it is real: **one request in eighty** before
caching, none in a hundred and sixty after. Caching the config parses `.env` once, at build
time, and takes the race out of the request path.

The cost is that `api/.env` is no longer read at runtime. **After editing it, run
`php artisan config:cache` again** or the change will not take effect. The test suite is
insulated from the cache by `APP_CONFIG_CACHE` in `phpunit.xml`, so `php artisan test` still
reads its own settings and still uses the test database — without that guard a test run
would have rebuilt the clinic's database instead of the test one.

```bash
cd web && npm install && npm run dev
```

Three processes in all: the engine on 8001, the API on 8000, the front end on 5173.

Open the front end at `http://localhost:5173`. Vite binds to IPv6 here, so
`http://127.0.0.1:5173` does not answer at all — that applies to the front end only; the
API and the engine both listen on `127.0.0.1`.

### Turn OPcache on

Not optional if you want the interface to feel quick. Without it PHP recompiles the whole
framework on every request: `/api/patients` measured 200ms with it off and 65ms with it on,
for a query the database answers in 1.2ms.

It ships with PHP and is simply commented out. In `php.ini` — `php --ini` says which one —
uncomment and set:

```ini
zend_extension=opcache
opcache.enable=1
opcache.enable_cli=1
opcache.memory_consumption=192
opcache.validate_timestamps=1
opcache.revalidate_freq=0
```

`enable_cli` is for artisan commands and the test suite; Apache uses `opcache.enable`. Keep
`validate_timestamps` on with `revalidate_freq=0`: PHP then checks whether a file changed on
each request and recompiles only when it has, which is what stops you debugging code you
already edited.

Restart Apache afterwards — a running server keeps the settings it booted with.

### Everything else

| Command | What it does |
|---|---|
| `python -m rag.ingest --dry-run` | What would be ingested, without touching the store |
| `python -m rag.ingest` | Rebuild the vector store — **stop the engine first**, see below |
| `python -m evaluation.run_eval --rules-only` | Score the triage agent against the labelled cases |
| `python -m pytest -q` | The Python test suite |
| `cd api && php artisan test` | The Laravel test suite (needs MySQL running) |
| `cd api && php artisan db:seed` | Reload the fixture record |

The engine keeps the reference library open for as long as it runs, so it holds a lock on
the store's files. Rebuilding the store while it is running fails on Windows — stop the
engine, run the ingest, start it again.

There were command-line front ends for consultations, report reading, SOAP notes, triage and
the record store. They are gone: the web application does all of it, and a second interface
onto the same clinical layer was two things to keep in step for no gain. The layer itself is
untouched — `chatbot/consultation.py`, `soap/note.py`, `report_reader/` and `triage/` are
what the engine calls.

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

Any visit can be written up as a SOAP note, at any stage, at any time — from the
consultation screen, or over the API:

```
GET /api/consultations/{visit}/soap
```

The engine builds it at `POST /soap` (`soap/note.py`), Laravel stores the edited version,
and `GET /api/consultations/{visit}/soap/saved` returns what was saved.

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
behind — the engine's own rule, travelling over HTTP rather than being restated by the
caller.

`GET /reference/workflow` serves the state machine so a front end can grey out what the
engine would refuse, rather than keeping its own copy of the transition table to drift
against.

Set `SERVICE_API_KEY` in `.env` and send it as `X-Service-Key`. The service answers
clinical questions about named patients: bind it to localhost, and do not expose the port.

## Patient triage

A separate agent, in `triage/`. It decides who in the waiting room is seen first. It
shares the record models and the model plumbing with the assistant and nothing else —
delete the directory and the consultation flow is untouched.

It runs when a nurse records vitals in the waiting room. The engine exposes it at
`POST /triage/assess`, one patient per call, and `GET /triage/rules` reports which rule set
is loaded and whether it has been verified.

The labelled cases in `data/triage_cases/cases.json` are what the evaluation scores against:

```bash
venv/Scripts/python -m evaluation.run_eval --rules-only
```

### How it decides

```
request → validation → NEWS2 → red flags → RULE FLOOR → AI → decision
                                                ↑              │
                                                └── may raise, never lower
```

The priority is `max(rule_floor, ai_adjustment)`, computed in one function in
`triage/decision.py`. Four deterministic outcomes set the floor — the NEWS2 band, a
single parameter scoring 3, any red flag that fired, and the floor for incomplete or
out-of-scope data — and the model's one lever raises a patient **to URGENT and no
further**.

That ceiling is a design decision worth knowing about. An earlier version escalated by
one level, which put a model-detected probable acute coronary syndrome at STANDARD —
too weak to be worth having. The model can now always get someone looked at promptly;
it can never, on its own, declare a CRITICAL. That claim belongs to the rules and the
clinician. It is configurable in `data/triage_rules.json`.

### The three safety properties

Enforced by code and covered by tests, in the same way as the assistant's:

1. **The model cannot lower a priority.** Not by policy — arithmetically. Every
   combination goes through `highest()`, which only ever returns the more urgent of its
   arguments, and `RawInterpretation` has no priority field for a model to fill in. A
   model that returns one at all fails schema validation and its whole contribution is
   discarded.
2. **The agent works without the model.** Every failure — no key, timeout, 429,
   malformed JSON — returns a result with `interpretation.available` false and the rules
   outcome intact. Nothing in `triage/interpret.py` raises. In the last full evaluation
   run the free-tier model was reachable for 23 of 32 cases; the other 9 were triaged
   correctly anyway.
3. **Nothing is invented.** A missing observation is reported, not imputed. It scores
   nothing rather than zero, because zero is the score for a value that was measured and
   found normal. A patient whose observations are incomplete cannot be ranked LOW.

### Where the numbers come from

`data/triage_rules.json` holds every clinical threshold. The engine contains none — it
looks values up and adds them together. The file is refused at load if its bands overlap,
leave a gap, or name a priority that does not exist, and there is **no degraded mode**:
a broken rule set stops the agent rather than letting it sort patients on defaults nobody
reviewed.

The scale is NEWS2 (Royal College of Physicians, 2017), which is validated for acutely
ill adults. Patients it is not validated for — anyone under 16, and anyone recorded as
pregnant — are not scored on it. They are returned as `OUT_OF_SCOPE` and escalated to a
clinician, which is deliberate over-triage and the honest cost of admitting the scale
does not apply.

**The thresholds are transcribed and not yet verified.** `verification_status` in the
rule file says so, `GET /triage/rules` reports it, and a test asserts the admission is
still there. Check them cell-by-cell against the RCP chart before the defence.

### Evaluation

```bash
venv/Scripts/python -m evaluation.run_eval --rules-only --repeat 3
```

32 synthetic cases, no real patient data. The headline metric is **critical
sensitivity**, not accuracy: in triage the two kinds of error are not comparable, and a
single accuracy figure averages away the asymmetry that makes the problem hard. The
harness exits non-zero if any CRITICAL case is missed, so it can gate a commit.

Agreement is currently 32/32 rules-only and 31/32 with the model — and that number
means less than it looks. The cases and the rules were written by the same authors, so
it measures internal consistency, not clinical validity. Real accuracy needs cases
labelled by someone who has not seen the rule set.

### Using it from a backend

One function, dict in and dict out:

```python
from triage.service import triage_payload, TriageInputError

result = triage_payload(request.json, profile=patient_or_none)
```

It imports no web framework and touches no database. `TriageInputError` carries a
readable message naming the offending field; every other failure, including the model
being unreachable, produces a valid result. Persistence, when you want it, is
`storage/triage_repository.py` behind the `TriageRepository` protocol — a triage result
is append-only, and saving one twice raises rather than quietly replacing an earlier
decision.

Over HTTP it is `POST /triage/assess`, taking the triage form, an optional profile, and
`rules_only`. Deliberately not a `Chart`: triage happens before a visit exists, and
requiring one would mean inventing an empty encounter for every walk-in.

`GET /reference/triage-rules` serves the priority ladder, the recommended actions and the
red-flag list, so a front end can explain a priority without keeping its own copy of the
rules to drift against. `GET /health` reports the rule set version and whether its
thresholds have been verified.

### Sorting a queue with it

Every result carries `priority_rank`: `CRITICAL` 3, `URGENT` 2, `STANDARD` 1, `LOW` 0.

```sql
ORDER BY priority_rank DESC, arrived_at ASC
```

That is the whole ordering policy, and shipping the integer is what keeps it here rather
than in the backend's language. It is derived from `priority` on every validation, so a
stored row whose rank no longer matches its priority is corrected on read instead of
sorting wrongly for the rest of the day.

Three things the backend has to get right, none of which the engine can enforce for it:

- **An unscored patient is not a low-priority patient.** A row with no observations has no
  rank. Sort those last and label them "not scored" — ranking someone LOW because nobody
  measured them is the failure most likely to hurt a real person.
- **Nothing runs on a timer.** Triage runs once per patient per set of observations, and
  the ordering is a sort over stored ranks. A new arrival does not re-score anyone else,
  because nobody else's observations changed.
- **Deterioration is not detected.** A score is a function of the observations; re-running
  it on unchanged observations returns the same answer. What the agent offers instead is
  staleness — `retriage_interval_minutes` per priority in the rule file — which is a
  prompt for a human to go and measure someone, not a substitute for it.

### Wired into this project's backend

Live in the Laravel API and the React front end, on the nurse's queue:

| Piece | Where |
|---|---|
| The engine call, and the only thing that makes it | `api/app/Engine/TriageRunner.php` |
| Scoring on save, re-triage, the override | `api/app/Http/Controllers/WaitingRoomController.php` |
| Priority badge, the reasoning behind it, the override form | `web/src/pages/WaitingRoomPage.jsx` |
| Integration tests | `api/tests/Feature/TriageTest.php` |

Recording vitals is what triages: one action for the nurse, because a screen with a
separate "score them now" button is a screen where half the queue ends up unscored. The
form asks for the chief complaint and for consciousness and supplemental oxygen alongside
the numbers — the first because half the priority comes from it, the other two because
they are NEWS2 parameters and without them the scale can only ever score five of seven.

If the engine is unreachable the nurse's save still succeeds and the row stays unscored.
Triage is an enrichment of the queue, and it does not get to break it.

The queue screen can be sorted **by priority** (the default) or **by arrival**, because
"who is next" and "where is Mrs Haddad" are different questions and a list that only
answers the first gets worked around. The switch changes the order and nothing else —
priorities are still computed, still stored, and still shown on every row — so nobody
loses clinical information by using it. The preference is per browser, not per clinic: one
person looking for a patient must not reorder everyone else's screen. While arrival order
is on and someone urgent is waiting, the list says so and offers the way back.

The clinician's override is stored beside the agent's answer and never over it, with a
reason the API requires. `suggested_priority` keeps what the agent said, `nurse_priority`
what the clinician chose, and both travel to the screen — the same shape as the
assistant's differential sitting beside the physician's working diagnosis, and for the
same reason. Set `ENGINE_TRIAGE_RULES_ONLY=true` to run the queue without the model.

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
checking code *format* only. `GET /reference/icd10` reports which mode it is in.

Keep the file grouped by ICD chapter, the way it already is: all the J codes, then A, U, C,
I, R, T. Nothing enforces it and nothing breaks if you don't — the search orders by code
anyway — but a chapter split in two is hard to read and easy to duplicate into.

**Adding a code takes three steps, and skipping the third is the one that bites.** The
assistant validates against the file; the doctor's search box reads the database, and they
are not the same thing.

1. Add it to `data/icd10_respiratory.json`, then restart the engine so it reloads the list.
2. Copy the file to `api/database/seed/icd10_respiratory.json` — the backend's own copy, so
   seeding needs nothing outside `api/`.
3. Load it into the database:

```bash
cd api && php artisan db:seed --class=Icd10CodeSeeder
```

That seeder does the code list and nothing else. Run it as often as you like: it is an
upsert keyed on the code, so it adds what is new and refreshes what changed. Use it rather
than a full `php artisan db:seed`, which also resets the fixture patients to their file
state and would discard any edits made to those charts through the application.

It never removes codes. One deleted from the file stays in the table, because a past visit
coded with it would otherwise become unreadable.

### Test patients

Edit `api/database/seed/patients.json` and re-run `cd api && php artisan db:seed`. The file
is the master copy; the database is a loaded copy of it.

It lives inside the backend on purpose. Seeding reads nothing outside `api/`, so the backend
can be checked out or deployed on its own and still populate its own database with no other
service running.

## Architecture

```
chatbot/          talking to the model — prompt, one assessment call.
                  llm.py is shared plumbing: both AI modules call run_structured
clinical/         clinical reasoning — differential, ICD-10, investigations,
                  medications + safety screen, consultation state machine, session
triage/           the triage agent — validation, NEWS2, red flags, AI interpretation,
                  decision, waiting room. Its own module, its own rule file
patient/          the record — profile, visit, report, and the context builder
rag/              the reference library — curation, chunking, embedding, retrieval
report_reader/    reports — extraction (transcription only) and analysis (separate)
soap/             SOAP notes — a rendering of the record, no reasoning, standalone
storage/          the repository protocols, and a SQLite implementation of them.
                  Nothing in the running system uses the SQLite side any more —
                  the backend persists. It is kept as the test suite's store
service/          the engine over HTTP — stateless, stores nothing, adds no logic
evaluation/       the triage evaluation harness — synthetic cases only
```

`soap/` depends on the patient models and nothing depends on it, so it can be removed
without affecting the consultation flow.

**The two AI modules are separate.** `triage/` imports `chatbot.llm` for the model call
and `clinical.json_reply` for JSON recovery — shared plumbing, not clinical logic — and
nothing else. It has its own rule file, its own prompt, its own tests, its own storage
protocol. Nothing in `clinical/` or `chatbot/` imports it, and deleting the directory
leaves the consultation flow working.

The triage agent is not yet exposed over HTTP. `triage.service.triage_payload` already
matches the service's contract — a dict in, a dict out, no database, no state — so the
route is a thin wrapper when it is wanted.

Two boundaries are load-bearing:

**`storage/repository.py` is a seam.** The clinical layer depends only on those
protocols, so replacing SQLite with a web backend meant writing one adapter and changing
nothing else — `service/repositories.py` is that adapter, and it is 80 lines. Both
implementations still exist, which is the demonstration: the same clinical code runs against
a database and against a dictionary filled from an HTTP request.

**Extraction and analysis are separate.** `report_reader/extractor.py` transcribes and
interprets nothing, so the stored JSON stays a faithful record of what was printed.
`report_reader/analyzer.py` interprets, only when the physician asks it to. Within the
analyser, deciding a value is out of range is arithmetic done in code — the model only
describes what the flags show.

## Configuration

Three services, three environment files, none of them in the repository:

| File | Example | Configures |
|---|---|---|
| `.env` | `.env.example` | the engine — model keys, data paths, `SERVICE_API_KEY` |
| `api/.env` | `api/.env.example` | the API — database credentials, `ENGINE_URL`, `ENGINE_KEY` |
| `web/.env` | `web/.env.example` | the front end — `VITE_API_URL` |

`ENGINE_KEY` on the Laravel side must match `SERVICE_API_KEY` on the engine's, and an empty
value on both means no check — only safe while the port is bound to localhost.

The rest of this section is the engine's file. Copy `.env.example` to `.env` and add an
OpenRouter API key. Defaults use a free model.

| Setting | Default | Meaning |
|---|---|---|
| `OPENROUTER_MODEL` | `google/gemma-4-31b-it:free` | The reasoning model |
| `VLM_MODEL` | `google/gemma-4-31b-it:free` | The report-reading model |
| `CONTEXT_RECENT_VISITS` | 3 | Past visits summarised into the prompt |
| `CONTEXT_RECENT_REPORTS` | 4 | Past reports summarised into the prompt |
| `EVIDENCE_CHUNKS` | 6 | Reference excerpts retrieved per assessment |
| `TRIAGE_MODEL` | `OPENROUTER_MODEL` | The triage interpretation model |
| `TRIAGE_LLM_TIMEOUT` | 20 | Seconds before triage gives up on the model and returns the rules result |
| `TRIAGE_LLM_RETRIES` | 1 | Lower than the assistant's 6 — triage degrades gracefully, so waiting out a 429 costs more than it buys |
| `TRIAGE_RULES_ONLY` | 0 | Set to 1 to run triage with no model at all |

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
- **The triage thresholds are transcribed, not verified.** The NEWS2 tables in
  `data/triage_rules.json` were written from the published scale and have not been
  checked cell-by-cell against the RCP document. The file says so and `GET /triage/rules`
  reports it.
- **The triage red-flag list is a phrase list.** It catches "chest pain" and misses "my
  chest feels like someone is sitting on it" — which is what the AI layer is for, and
  why that layer can raise a priority. Negation is scoped to a short window before the
  phrase, so "chest pain ruled out last week" still fires. Over-triage, by design.
- **NEWS2 is a deterioration score, not a front-door triage scale.** It is validated for
  acutely ill adults in hospital, and using it to order a waiting room is this project's
  own mapping, stated explicitly in the `escalation` block of the rule file. A full ED
  triage system would use a purpose-built scale (ESI, Manchester, CTAS).
- **Triage evaluation is internal.** The 32 cases and the rules were written by the same
  authors, so the agreement figures measure consistency, not clinical accuracy.
- **Triage covers adults only.** Under-16s and pregnant patients are detected and
  escalated rather than scored, which is safe but crude.
- **No authentication, no audit of who did what.** Single-user prototype. The triage
  results table records what the system decided and what a clinician overrode it with,
  but not who that clinician was beyond a free-text name.
- **Records cannot be corrected.** Notes are append-only and a completed visit cannot be
  reopened; a mistake means a new visit.

## Disclaimer

Decision support for qualified clinicians. Not a medical device, not validated for
clinical use, and not a substitute for professional judgement. The test patients are
fictional and contain no real patient data.
