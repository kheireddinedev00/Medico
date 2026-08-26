<?php

namespace App\Http\Controllers;

use App\Engine\ChartBuilder;
use App\Engine\DraftVisits;
use App\Engine\EngineClient;
use App\Engine\VisitWriter;
use App\Models\AssistantRun;
use App\Models\Patient;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * The consultation workflow, as HTTP.
 *
 * Every method here follows the same three steps: build the chart, ask the engine, store
 * what comes back. None of them evaluates a clinical rule. There is no `if ($visit->status
 * === ...)` in this file and there should never be one — the engine refuses illegal
 * transitions and its refusal arrives as a 409 that this application simply passes on.
 *
 * The suggestion endpoints (`assess`, `codes`, `investigations`, `medications`) write to
 * `assistant_runs`, never to the visit's decision fields. The decision endpoints write to
 * the visit, and they are the only ones that do. That division is the whole point of the
 * schema, and it is enforced here by there being no code that crosses it.
 */
class ConsultationController extends Controller
{
    public function __construct(private readonly EngineClient $engine)
    {
    }

    /**
     * Open a consultation.
     *
     * Returns a visit that is not in the database, and says so. It is held as a draft until
     * the physician commits to a diagnosis; walk away now and nothing remains.
     */
    public function store(Request $request): JsonResponse
    {
        $data = $request->validate(['patient_id' => ['required', 'string', 'exists:patients,id']]);
        $patient = Patient::findOrFail($data['patient_id']);

        $result = $this->engine->startVisit(ChartBuilder::forNewVisit($patient));
        DraftVisits::put($request->user()->id, $result['visit']);

        return response()->json([
            'visit' => $result['visit'],
            'persisted' => false,
            'message' => 'Consultation opened. Nothing is recorded until a diagnosis is chosen.',
        ], 201);
    }

    /**
     * Reopen a consultation that is already in the record.
     *
     * This is how a visit left at WAITING_FOR_TESTS is picked up again days later, when the
     * results arrive. Without it a visit could be opened, put on hold and never finished —
     * the state machine has always allowed the return, but nothing could reach it.
     *
     * A completed visit is not reopened. The engine refuses, and it is right to: a finished
     * encounter is not edited, and a new concern is a new visit.
     */
    public function show(Request $request, string $visitId): JsonResponse
    {
        $visit = Visit::with(['differentials', 'investigations', 'prescriptions', 'reports'])
            ->find($visitId);

        // Not in the record yet, so look for the draft this clinician is holding. Without
        // this, opening a consultation and then loading its own URL would 404 — the visit
        // exists, it simply has not earned a database row until a diagnosis is chosen.
        if (! $visit) {
            $draft = DraftVisits::get($request->user()->id, $visitId);

            abort_if($draft === null, 404, 'No such consultation, or the draft has expired.');

            $patient = Patient::findOrFail($draft['patient_id']);

            return response()->json([
                'visit' => $draft,
                'persisted' => false,
                'patient' => ['id' => $patient->id, 'full_name' => $patient->full_name],
                'reports' => [],
            ]);
        }

        return response()->json([
            'visit' => $visit->toEngineVisit(),
            'persisted' => true,
            'investigations' => $this->investigationsOf($visit),
            'patient' => $visit->patient()->select(['id', 'full_name'])->first(),
            // Reports attached to this visit, so the doctor can see what is outstanding and
            // which uploads have actually been analysed.
            'reports' => $visit->reports->map(fn ($r) => [
                'id' => $r->id,
                'label' => $r->label(),
                'kind' => $r->kind,
                'analysed' => $r->isAnalysed(),
            ]),
        ]);
    }

    /** Every visit still open, for the doctor's "in progress" list. */
    public function open(Request $request): JsonResponse
    {
        $visits = Visit::with('patient:id,full_name')
            ->where('status', '!=', 'COMPLETED')
            ->orderByDesc('updated_at')
            ->get();

        return response()->json([
            'visits' => $visits->map(fn (Visit $v) => [
                'id' => $v->id,
                'patient' => ['id' => $v->patient->id, 'full_name' => $v->patient->full_name],
                'status' => $v->status,
                'chief_complaint' => $v->chief_complaint,
                'working_diagnosis' => $v->working_diagnosis_label,
                'updated_at' => $v->updated_at,
                'awaiting_results' => $v->status === 'WAITING_FOR_TESTS',
            ]),
        ]);
    }

    /**
     * Delete a visit.
     *
     * Worth being plain about: the rest of this system is append-only. A completed visit
     * cannot be reopened and a mistaken entry is normally corrected by a new visit, because
     * a record that can be quietly rewritten is a record nobody can rely on afterwards.
     *
     * This exists anyway, because a test system fills up with junk consultations and
     * clearing them is a real need. Two things make it survivable: the whole visit is
     * written into the audit log before it goes, and reports are detached rather than
     * destroyed — a result that arrived is the patient's, whatever happens to the
     * encounter that ordered it.
     */
    public function destroy(Request $request, string $visitId): JsonResponse
    {
        $visit = Visit::with(['differentials', 'investigations', 'prescriptions'])->findOrFail($visitId);

        Auditor::record(
            $request->user()->id,
            'consultation.deleted',
            $visit,
            $visit->toEngineVisit(),
            null,
            $request->ip(),
        );

        DB::transaction(function () use ($visit) {
            // Detach first. The foreign key nulls on delete anyway; doing it explicitly
            // means the intent is visible here rather than hidden in a migration.
            $visit->reports()->update(['visit_id' => null]);
            $visit->delete();
        });

        return response()->json(['deleted' => true]);
    }

    /**
     * The doctor is finished with the patient in the room.
     *
     * Distinct from navigating away, and the difference is the point. A doctor who goes back
     * to the patient list has not finished — they are checking something, and the patient is
     * still sitting in the clinic, so the queue must still show them. This endpoint is the
     * other thing: the patient can go, and the row closes.
     *
     * The visit itself is untouched. One left at WAITING_FOR_TESTS stays open and resumable;
     * ending the attendance is about the room, not about the encounter.
     */
    public function leave(Request $request, string $visitId): JsonResponse
    {
        [$patient] = $this->chartFor($request, $visitId);

        $entry = WaitingRoomController::currentAttendance($patient->id, $visitId);

        if ($entry) {
            $entry->update(['status' => 'completed', 'seen_at' => $entry->seen_at ?? now()]);
            Auditor::record($request->user()->id, 'waiting_room.released', $entry, null, null, $request->ip());
        }

        return response()->json([
            'released' => $entry !== null,
            'message' => $entry
                ? 'The patient has left the waiting room. The visit stays open.'
                : 'No waiting-room entry to close.',
        ]);
    }

    /** Record what the doctor observed. Still not a record. */
    public function findings(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'chief_complaint' => ['nullable', 'string'],
            'symptoms' => ['array'],
            'symptoms.*' => ['string'],
            'physical_exam' => ['nullable', 'string'],
            'observations' => ['nullable', 'string'],
            'vitals' => ['array'],
            'vitals.temperature_c' => ['nullable', 'numeric'],
            'vitals.heart_rate' => ['nullable', 'integer'],
            'vitals.respiratory_rate' => ['nullable', 'integer'],
            // A string, not two numbers — "128/76 (left arm)" is a real chart entry.
            'vitals.blood_pressure' => ['nullable', 'string'],
            'vitals.spo2' => ['nullable', 'numeric'],
            'vitals.weight_kg' => ['nullable', 'numeric'],
        ]);

        [$patient, $chart] = $this->chartFor($request, $visitId);

        $result = $this->engine->transition('findings', [
            'chart' => $chart,
            'findings' => array_merge($data, ['patient_id' => $patient->id]),
        ]);

        return $this->persist($request, $patient, $result);
    }

    // --- the assistant: suggestions, recorded as suggestions ---------------------

    /**
     * A differential for today's findings.
     *
     * The visit comes back with the suggestion attached, but the physician's decision
     * fields are untouched — the engine has no way to write them, and neither does this.
     */
    public function assess(Request $request, string $visitId): JsonResponse
    {
        [$patient, $chart] = $this->chartFor($request, $visitId);
        $result = $this->engine->assess($chart);

        $stored = $this->persist($request, $patient, $result, respond: false);

        $this->recordRun($request, $result['visit']['id'], 'differential', $result['assessment']);

        return response()->json([
            'assessment' => $result['assessment'],
            'visit' => $stored['visit'],
            'persisted' => $stored['persisted'],
        ]);
    }

    public function codes(Request $request, string $visitId): JsonResponse
    {
        [, $chart] = $this->chartFor($request, $visitId);
        $advice = $this->engine->suggestCodes($chart);
        $this->recordRun($request, $visitId, 'icd10', $advice);

        return response()->json($advice);
    }

    public function investigations(Request $request, string $visitId): JsonResponse
    {
        [, $chart] = $this->chartFor($request, $visitId);
        $advice = $this->engine->suggestInvestigations($chart);
        $this->recordRun($request, $visitId, 'investigations', $advice);

        return response()->json($advice);
    }

    /**
     * Treatment options, already screened against the record.
     *
     * Withheld drugs are stored and returned, not quietly dropped. A drug suppressed for an
     * allergy that leaves no trace is indistinguishable from one the model never proposed,
     * and showing the difference is the point of the screen.
     */
    public function medications(Request $request, string $visitId): JsonResponse
    {
        [, $chart] = $this->chartFor($request, $visitId);
        $advice = $this->engine->suggestMedications($chart);

        $run = $this->recordRun($request, $visitId, 'medications', $advice);

        // The engine returns two flat lists — `withheld` and `cautions` — each entry naming
        // the drug in `medication`. Both are stored: a drug suppressed for an allergy that
        // leaves no trace is indistinguishable from one the model never suggested.
        if ($run) {
            foreach (['withheld', 'cautions'] as $bucket) {
                foreach ($advice[$bucket] ?? [] as $warning) {
                    $run->warnings()->create([
                        'drug' => $warning['medication'] ?? 'unknown',
                        'severity' => $warning['severity'] ?? ($bucket === 'withheld' ? 'withheld' : 'caution'),
                        'reason' => $warning['reason'] ?? '',
                    ]);
                }
            }
        }

        return response()->json($advice);
    }

    /**
     * Screen drugs the physician typed themselves.
     *
     * The assistant's suggestions are screened before anyone sees them. A drug the doctor
     * writes in bypasses that entirely — which is correct, because they are the
     * decision-maker — but a prescriber working from memory can still miss an allergy
     * recorded months ago by someone else. So the same screen runs and reports back.
     *
     * `blocked` here does not mean refused. It means the screen would have withheld this
     * drug had the model proposed it, and the physician should see why before signing.
     */
    public function checkMedications(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'names' => ['required', 'array', 'min:1'],
            'names.*' => ['required', 'string', 'max:255'],
        ]);

        [, $chart] = $this->chartFor($request, $visitId);

        return response()->json($this->engine->checkMedications($chart, $data['names']));
    }

    // --- the physician's decisions -----------------------------------------------

    public function selectDiagnosis(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'label' => ['required', 'string', 'max:255'],
            'icd10_code' => ['nullable', 'string', 'exists:icd10_codes,code'],
            'reasoning' => ['nullable', 'string'],
        ]);

        return $this->run($request, $visitId, 'select-diagnosis', $data);
    }

    public function setCode(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            // Validated against the curated list, so a code that does not exist cannot be
            // attached even if a client sends one directly.
            'code' => ['required', 'string', 'exists:icd10_codes,code'],
        ]);

        return $this->run($request, $visitId, 'icd10-code', $data);
    }

    public function orderInvestigations(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'investigations' => ['required', 'array', 'min:1'],
            'investigations.*.name' => ['required', 'string'],
            'investigations.*.category' => ['nullable', 'in:laboratory,radiology,other'],
            'investigations.*.rationale' => ['nullable', 'string'],
        ]);

        return $this->run($request, $visitId, 'order-investigations', $data);
    }

    public function recordResults(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'summary' => ['required', 'string'],
            'resulted' => ['nullable', 'array'],
            'resulted.*' => ['string'],
        ]);

        return $this->run($request, $visitId, 'record-results', $data);
    }

    public function recordResultsFromReports(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'report_ids' => ['required', 'array', 'min:1'],
            'report_ids.*' => ['string', 'exists:reports,id'],
            'resulted' => ['nullable', 'array'],
            'resulted.*' => ['string'],
        ]);

        return $this->run($request, $visitId, 'record-results-from-reports', $data);
    }

    public function reviseDiagnosis(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'label' => ['required', 'string', 'max:255'],
            'icd10_code' => ['nullable', 'string', 'exists:icd10_codes,code'],
            'reasoning' => ['nullable', 'string'],
        ]);

        return $this->run($request, $visitId, 'revise-diagnosis', $data);
    }

    public function prescribe(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate([
            'medications' => ['array'],
            'medications.*.name' => ['required', 'string'],
            'medications.*.dose' => ['nullable', 'string'],
            'medications.*.frequency' => ['nullable', 'string'],
            'medications.*.duration' => ['nullable', 'string'],
            'medications.*.rationale' => ['nullable', 'string'],
        ]);

        return $this->run($request, $visitId, 'prescribe', $data);
    }

    public function followUp(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate(['plan' => ['nullable', 'string']]);

        return $this->run($request, $visitId, 'follow-up', $data);
    }

    public function note(Request $request, string $visitId): JsonResponse
    {
        $data = $request->validate(['note' => ['required', 'string']]);

        return $this->run($request, $visitId, 'note', $data);
    }

    /** Option A — investigations before treatment. */
    public function investigate(Request $request, string $visitId): JsonResponse
    {
        return $this->run($request, $visitId, 'investigate');
    }

    /** Option B, or a change of mind after opening the list. */
    public function skipInvestigations(Request $request, string $visitId): JsonResponse
    {
        return $this->run($request, $visitId, 'skip-investigations');
    }

    public function orderMoreInvestigations(Request $request, string $visitId): JsonResponse
    {
        return $this->run($request, $visitId, 'order-more-investigations');
    }

    public function treat(Request $request, string $visitId): JsonResponse
    {
        return $this->run($request, $visitId, 'treat');
    }

    public function complete(Request $request, string $visitId): JsonResponse
    {
        return $this->run($request, $visitId, 'complete');
    }

    // --- reading ------------------------------------------------------------------

    /** Where this visit may go next, straight from the engine's transition table. */
    public function nextStates(Request $request, string $visitId): JsonResponse
    {
        [, $chart] = $this->chartFor($request, $visitId);

        return response()->json($this->engine->transition('next-states', ['chart' => $chart]));
    }

    /**
     * The SOAP note. Built on request and never stored, so it cannot go stale against the
     * chart it describes.
     */
    public function soap(Request $request, string $visitId): JsonResponse
    {
        [, $chart] = $this->chartFor($request, $visitId);

        return response()->json($this->engine->soap(
            $chart,
            $request->boolean('include_assistant_differential', true),
        ));
    }

    // --- internals ----------------------------------------------------------------

    /**
     * Find the consultation, wherever it currently lives.
     *
     * A saved visit comes from the database. One that has not reached a diagnosis yet comes
     * from the draft cache, keyed to the clinician holding it — never from the request body,
     * which would let a client choose its own workflow state.
     *
     * @return array{0: Patient, 1: array}
     */
    private function chartFor(Request $request, string $visitId): array
    {
        $visit = Visit::find($visitId);

        if ($visit) {
            return [$visit->patient, ChartBuilder::forVisit($visit)];
        }

        $draft = DraftVisits::get($request->user()->id, $visitId);

        abort_if($draft === null, 404, 'No such consultation, or the draft has expired.');

        $patient = Patient::findOrFail($draft['patient_id']);

        return [$patient, ChartBuilder::forUnsavedVisit($patient, $draft)];
    }

    /** Build the chart, run one transition, store whatever the engine hands back. */
    private function run(Request $request, string $visitId, string $action, array $payload = []): JsonResponse
    {
        [$patient, $chart] = $this->chartFor($request, $visitId);

        $before = $chart['visit']['status'];
        $result = $this->engine->transition($action, array_merge($payload, ['chart' => $chart]));
        $response = $this->persist($request, $patient, $result);

        // A finished encounter closes its place in the queue. Vitals belong to an
        // attendance, so leaving the row open would mean the next consultation for this
        // patient is offered readings taken on an earlier day.
        if (($result['visit']['status'] ?? null) === 'COMPLETED') {
            WaitingRoomController::closeFor($result['visit']['id']);
        }

        // Logged only once the visit is real. A decision on a consultation nobody committed
        // to is not part of the record, and an audit row for it would imply otherwise.
        if ($result['persist'] ?? false) {
            Auditor::record(
                $request->user()->id,
                "consultation.{$action}",
                Visit::find($result['visit']['id']),
                ['status' => $before],
                ['status' => $result['visit']['status']] + $payload,
                $request->ip(),
            );
        }

        return $response;
    }

    /**
     * Honour the engine's `persist` flag.
     *
     * True and the visit is written; false and it stays a draft. This application does not
     * form its own opinion about which — the rule that an abandoned consultation leaves no
     * record lives in the engine, and restating it here is how the two would drift.
     */
    private function persist(Request $request, Patient $patient, array $result, bool $respond = true): mixed
    {
        $userId = $request->user()->id;
        $saved = VisitWriter::apply($patient, $result);

        if ($saved) {
            if ($saved->doctor_id === null) {
                $saved->forceFill(['doctor_id' => $userId])->saveQuietly();
            }
            DraftVisits::forget($userId, $saved->id);

            // The consultation just became a record, so the queue row's provisional pointer
            // graduates into the real foreign key. Leaving it in the draft column would mean
            // the entry no longer names a visit that exists.
            WaitingRoomEntry::where('draft_visit_id', $saved->id)
                ->update(['draft_visit_id' => null, 'visit_id' => $saved->id]);
        } else {
            DraftVisits::put($userId, $result['visit']);
        }

        $payload = [
            'visit' => $saved ? $saved->toEngineVisit() : $result['visit'],
            'persisted' => (bool) $saved,
            // Alongside, not inside. The engine's Investigation has no id — it does not need
            // one — but the client does, to file an uploaded result against the test it
            // answers. Adding a field to the engine shape to solve a UI problem would be the
            // wrong direction.
            'investigations' => $saved ? $this->investigationsOf($saved) : [],
        ];

        return $respond ? response()->json($payload) : $payload;
    }

    /** The visit's ordered tests, with the ids an upload needs to attach itself to one. */
    private function investigationsOf(Visit $visit): array
    {
        return $visit->investigations()->orderBy('id')->get()
            ->map(fn ($i) => [
                'id' => $i->id,
                'name' => $i->name,
                'category' => $i->category,
                'status' => $i->status,
                'rationale' => $i->rationale,
            ])->all();
    }

    /**
     * Log what the assistant said.
     *
     * Append-only, and only for a visit that exists — a suggestion about a consultation
     * nobody committed to is not part of the record either. Failure to log must not fail
     * the clinical request, so this returns null rather than throwing.
     */
    private function recordRun(Request $request, string $visitId, string $kind, array $response): ?AssistantRun
    {
        if (! Visit::whereKey($visitId)->exists()) {
            return null;
        }

        return AssistantRun::create([
            'visit_id' => $visitId,
            'kind' => $kind,
            'model' => config('engine.model_label', 'engine'),
            'raw_response' => $response,
            'evidence' => $response['sources'] ?? ($response['evidence'] ?? null),
            'requested_by' => $request->user()->id,
        ]);
    }
}
