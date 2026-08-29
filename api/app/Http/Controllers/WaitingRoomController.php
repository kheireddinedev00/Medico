<?php

namespace App\Http\Controllers;

use App\Engine\EngineClient;
use App\Engine\TriageRunner;
use App\Models\Patient;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * The nurse's queue.
 *
 * A patient arrives, a nurse records what they are here about and measures them, the triage
 * agent scores them, the doctor takes them through in priority order.
 *
 * Nothing here reasons clinically. The priority, its rank, its reasons and the recommended
 * action all come from the engine and are stored as they arrived; this controller decides
 * who is asked and what is kept, never what the answer is. `App\Engine\TriageRunner` is the
 * only thing that talks to the agent.
 *
 * Two properties are worth defending against the next person who edits this file:
 *
 * **An unscored patient is not a low-priority patient.** A row with no vitals sorts last
 * but is shown as "not scored", because ranking someone LOW on the basis that nobody
 * measured them is the failure mode most likely to hurt a real person.
 *
 * **The nurse's ordering and the agent's live side by side.** `nurse_priority` wins for
 * sorting; `suggested_priority` is never overwritten. That pair is the audit trail for
 * "the AI is not making the decisions here", and it is the same shape as the assistant's
 * differential sitting beside the physician's working diagnosis.
 */
class WaitingRoomController extends Controller
{
    /**
     * The current queue.
     *
     * Two orderings, chosen by the caller with `?order=`:
     *
     * **priority** (the default) — most urgent first, then longest wait. The ordering is
     * the engine's, not this method's: `suggested_priority` holds the integer rank the
     * agent returned as `priority_rank`, so the SQL sorts on a number whose meaning was
     * decided in one place. No PHP here knows that CRITICAL outranks URGENT.
     *
     * Within a priority it is first-come-first-served. Someone who deteriorates keeps
     * their original arrival time and so keeps their place among their new peers.
     *
     * Unscored rows sort last. `NULLS LAST` is spelled out rather than relied upon
     * because SQLite and MySQL disagree about where nulls go, and the disagreement would
     * put every unmeasured patient at the top of the queue on one of them.
     *
     * **arrival** — purely who came first, ignoring every priority.
     *
     * The second exists because "who is next" and "where is Mrs Haddad" are different
     * questions, and a queue that can only answer the first is annoying enough that
     * people work around it. It changes the order and nothing else: the priorities are
     * still computed, still stored, and still shown on every row. Turning the sort off
     * hides no clinical information — which is the property that makes offering the
     * switch defensible at all.
     */
    public function index(Request $request): JsonResponse
    {
        $status = $request->string('status')->value() ?: 'active';
        // Anything unrecognised falls back to priority. The safe default is the one that
        // puts the sickest patient first, so a typo in a query string cannot quietly
        // reorder the room by arrival.
        $order = $request->string('order')->value() === 'arrival' ? 'arrival' : 'priority';

        $entries = WaitingRoomEntry::with('patient:id,full_name,date_of_birth,age_years')
            ->when($status !== 'all', function ($q) use ($status) {
                // "active" is the default and means still in the clinic — waiting, or with
                // the doctor. Someone being seen has not left the room, and dropping them
                // from the list the moment a consultation opens makes the queue disagree
                // with what is actually happening.
                return $status === 'active'
                    ? $q->whereIn('status', ['waiting', 'in_consultation'])
                    : $q->where('status', $status);
            })
            ->when($order === 'priority', function ($q) {
                return $q
                    ->orderByRaw('CASE WHEN COALESCE(nurse_priority, suggested_priority) IS NULL THEN 1 ELSE 0 END')
                    ->orderByRaw('COALESCE(nurse_priority, suggested_priority) DESC');
            })
            ->orderBy('arrived_at')
            ->get();

        return response()->json([
            'order' => $order,
            // How many people are waiting at each priority, computed over the whole queue
            // rather than the page. The front end needs this to warn that a CRITICAL
            // patient is waiting while the list is sorted by arrival and therefore not
            // showing them at the top.
            'counts' => $this->countsByPriority($entries),
            'entries' => $entries->map(fn (WaitingRoomEntry $e) => $this->present($e)),
        ]);
    }

    /** How many are waiting at each priority, and how many have not been scored. */
    private function countsByPriority($entries): array
    {
        $counts = ['CRITICAL' => 0, 'URGENT' => 0, 'STANDARD' => 0, 'LOW' => 0, 'unscored' => 0];

        foreach ($entries as $entry) {
            $label = $entry->effectivePriorityLabel();
            $key = $label !== null && array_key_exists($label, $counts) ? $label : 'unscored';
            $counts[$key]++;
        }

        return $counts;
    }

    /**
     * One queue entry, as the front end needs it.
     *
     * The wait is formatted here rather than in the client so every screen says it the same
     * way: whole minutes for the first hour, then hours to one decimal. "1.5 h" is
     * something a nurse can act on; "94 minutes" makes them do arithmetic, and "1.4833 h"
     * is noise dressed as precision.
     */
    private function present(WaitingRoomEntry $entry): array
    {
        $entry->loadMissing(['patient', 'visit']);
        $minutes = (int) round($entry->arrived_at?->diffInMinutes(now()) ?? 0);

        return [
            'id' => $entry->id,
            'patient' => [
                'id' => $entry->patient->id,
                'full_name' => $entry->patient->full_name,
                'age' => $entry->patient->date_of_birth?->age ?? $entry->patient->age_years,
            ],
            'status' => $entry->status,
            'arrived_at' => $entry->arrived_at,
            'waiting_minutes' => $minutes,
            'waiting_label' => $minutes < 60
                ? "{$minutes} min"
                : number_format($minutes / 60, 1).' h',
            'vitals' => $this->vitalsOf($entry),
            'vitals_taken_at' => $entry->vitals_taken_at,
            'vitals_recorded' => $entry->vitals_taken_at !== null,

            // Null means a new problem; set means the nurse marked them as back about an
            // existing visit, and the doctor should resume rather than open a second one.
            'visit_id' => $entry->visit_id,
            // What the doctor should be taken to — a saved visit, or the draft in flight.
            'active_visit_id' => $entry->visit_id ?? $entry->draft_visit_id,
            'visit' => $entry->visit ? [
                'id' => $entry->visit->id,
                'status' => $entry->visit->status,
                'chief_complaint' => $entry->visit->chief_complaint,
                'working_diagnosis' => $entry->visit->working_diagnosis_label,
            ] : null,

            // What the patient is here about. Half of the triage decision.
            'chief_complaint' => $entry->chief_complaint,
            'triage_notes' => $entry->triage_notes,

            /*
             * Observations that triage needs and the consultation's Vitals model does not
             * have. Kept out of `vitals` deliberately: that array is posted to the engine
             * as a `Vitals`, which is declared extra="forbid", so an extra key there would
             * turn the doctor's prefill into a 422.
             */
            'triage_observations' => [
                'on_oxygen' => $entry->on_oxygen,
                'oxygen_delivery' => $entry->oxygen_delivery,
                'consciousness' => $entry->consciousness,
                'is_pregnant' => $entry->is_pregnant,
                'hypercapnic_target_range' => (bool) $entry->hypercapnic_target_range,
            ],

            'triage' => $this->triageOf($entry),
        ];
    }

    /**
     * The triage decision, as the queue screen needs it.
     *
     * `scored` false means the agent has not run — no vitals yet, or the engine was
     * unreachable when they were taken. The UI must render that as an absence, not as a
     * priority, which is why there is a boolean here rather than a null priority the
     * front end has to remember to check.
     */
    private function triageOf(WaitingRoomEntry $entry): array
    {
        $result = $entry->triage_result;

        return [
            'scored' => $entry->suggested_priority !== null,
            // What the queue is sorted on, and the word for it. Both from the engine.
            'priority' => $entry->effectivePriorityLabel(),
            'priority_rank' => $entry->effectivePriority(),
            'reason' => $entry->priority_reason,
            'computed_at' => $entry->priority_computed_at,
            'status' => $entry->triage_status,
            'ruleset_version' => $entry->triage_ruleset_version,

            // True when the model was unreachable and the rules alone decided. The result
            // is complete and valid; the badge just stops it looking like more than it is.
            'degraded' => (bool) $entry->triage_degraded,

            // The agent's own answer, kept visible even when a nurse has overridden it —
            // a disagreement nobody can see is not an audit trail.
            'suggested_priority' => $entry->suggested_priority_label,
            'suggested_priority_rank' => $entry->suggested_priority,

            'override' => $entry->nurse_priority === null ? null : [
                'priority' => $entry->nurse_priority_label,
                'priority_rank' => $entry->nurse_priority,
                'reason' => $entry->nurse_priority_reason,
                'at' => $entry->nurse_priority_at,
            ],

            // The detail behind the badge, for the row's expanded view.
            'reasons' => $result['reasons'] ?? [],
            'concerning_findings' => $result['concerning_findings'] ?? [],
            'missing_information' => $result['missing_information'] ?? [],
            'data_quality_issues' => $result['data_quality_issues'] ?? [],
            'recommended_action' => $result['recommended_action'] ?? null,
            'news2' => isset($result['news2']) ? [
                'aggregate' => $result['news2']['aggregate'],
                'scored_count' => $result['news2']['scored_count'],
                'expected_count' => $result['news2']['expected_count'],
                'single_parameter_red' => $result['news2']['single_parameter_red'],
            ] : null,
            'red_flags' => collect($result['red_flags'] ?? [])
                ->map(fn ($f) => ['label' => $f['label'], 'floor' => $f['floor']])
                ->all(),
            'ai_escalated' => $result['ai_escalated'] ?? false,
            'ai_summary' => $result['interpretation']['summary'] ?? null,
        ];
    }

    /**
     * Register an arrival.
     *
     * `visit_id` is the nurse saying why this person is here: left null they are a new
     * problem and the doctor opens a fresh consultation; set to an open visit they have
     * come back about that one — results, a follow-up — and the doctor resumes it rather
     * than starting a second encounter for the same episode.
     *
     * Getting that wrong is how a chart ends up with two half-finished visits for one
     * illness, so the queue asks rather than guesses.
     */
    public function store(Request $request): JsonResponse
    {
        $data = $request->validate([
            'patient_id' => ['required', 'string', 'exists:patients,id'],
            'visit_id' => ['nullable', 'string', 'exists:visits,id'],
        ]);

        // A patient already in the queue is not added twice — a duplicate row would make
        // the same person appear as two people waiting.
        $existing = WaitingRoomEntry::where('patient_id', $data['patient_id'])
            ->where('status', 'waiting')
            ->first();

        if ($existing) {
            return response()->json([
                'entry' => $this->present($existing),
                'message' => 'That patient is already in the waiting room.',
            ], 200);
        }

        $entry = WaitingRoomEntry::create([
            'patient_id' => $data['patient_id'],
            'visit_id' => $data['visit_id'] ?? null,
            'nurse_id' => $request->user()->id,
            'arrived_at' => now(),
            'status' => 'waiting',
        ]);

        Auditor::record($request->user()->id, 'waiting_room.arrived', $entry, null, $entry->toArray(), $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())], 201);
    }

    /** Change what this arrival is about — a new problem, or an open visit to resume. */
    public function setVisit(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $data = $request->validate([
            'visit_id' => ['nullable', 'string', 'exists:visits,id'],
        ]);

        if (! empty($data['visit_id'])) {
            $visit = Visit::findOrFail($data['visit_id']);

            abort_if(
                $visit->patient_id !== $entry->patient_id,
                422,
                'That visit belongs to a different patient.',
            );
            // A finished encounter is not reopened; coming back about it is a new visit.
            abort_if($visit->status === 'COMPLETED', 422, 'That visit is already completed.');
        }

        $entry->update(['visit_id' => $data['visit_id'] ?: null]);
        Auditor::record($request->user()->id, 'waiting_room.visit_set', $entry, null, $data, $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /** Open visits for this patient, so the nurse can say which one they are back about. */
    public function resumableVisits(Request $request, string $patientId): JsonResponse
    {
        $visits = Visit::where('patient_id', $patientId)
            ->where('status', '!=', 'COMPLETED')
            ->orderByDesc('created_at')
            ->get(['id', 'status', 'chief_complaint', 'working_diagnosis_label', 'created_at']);

        return response()->json(['visits' => $visits]);
    }

    /**
     * Record the vitals taken at triage.
     *
     * Every field is optional and absent means "not measured". That is not the same as a
     * normal reading, and neither this endpoint nor the screen above it should let the two
     * blur — a blank saturation is a question, not reassurance.
     */
    public function vitals(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $data = $request->validate([
            'temperature_c' => ['nullable', 'numeric', 'between:25,45'],
            'heart_rate' => ['nullable', 'integer', 'between:20,300'],
            'respiratory_rate' => ['nullable', 'integer', 'between:4,80'],
            'blood_pressure' => ['nullable', 'string', 'max:40'],
            'spo2' => ['nullable', 'numeric', 'between:50,100'],
            'weight_kg' => ['nullable', 'numeric', 'between:1,400'],
            'height_cm' => ['nullable', 'numeric', 'between:30,250'],

            // The two NEWS2 parameters that are not numbers. Both nullable: not asked and
            // asked-and-normal are different answers and the engine scores them
            // differently, so neither may be defaulted here.
            'on_oxygen' => ['nullable', 'boolean'],
            'oxygen_delivery' => ['nullable', 'string', 'max:80'],
            'consciousness' => ['nullable', 'in:alert,confusion,voice,pain,unresponsive'],

            // What the patient says is wrong, and the two facts that change which
            // thresholds apply.
            'chief_complaint' => ['nullable', 'string', 'max:1000'],
            'triage_notes' => ['nullable', 'string', 'max:2000'],
            'is_pregnant' => ['nullable', 'boolean'],
            // Only ever set from a documented prescribed 88-92% target. See the migration.
            'hypercapnic_target_range' => ['nullable', 'boolean'],
        ]);

        $before = $this->vitalsOf($entry);

        $entry->fill($data);
        $entry->vitals_taken_at = now();
        $entry->nurse_id = $request->user()->id;
        $entry->save();

        Auditor::record($request->user()->id, 'waiting_room.vitals', $entry, $before, $this->vitalsOf($entry), $request->ip());

        // Score them. Returns null and logs if the engine is down — the nurse's save has
        // already succeeded and must not be undone by an unavailable enrichment.
        $result = TriageRunner::run($entry);

        if ($result !== null) {
            // The agent's decision is a clinical artefact and is audited like one. Stored
            // with the rule set version, so a decision can still be explained after the
            // thresholds change.
            Auditor::record(
                $request->user()->id,
                'waiting_room.triaged',
                $entry,
                null,
                [
                    'priority' => $result['priority'],
                    'rule_priority' => $result['rule_priority'],
                    'ai_escalated' => $result['ai_escalated'],
                    'status' => $result['status'],
                    'ruleset_version' => $result['ruleset_version'],
                    'request_id' => $result['request_id'],
                ],
                $request->ip(),
            );
        }

        return response()->json([
            'entry' => $this->present($entry->fresh()),
            'vitals' => $this->vitalsOf($entry),
            // Null tells the front end to show "not scored" rather than leaving the last
            // priority on screen as though it were current.
            'triaged' => $result !== null,
        ]);
    }

    /**
     * Re-score a patient without re-entering their vitals.
     *
     * For the two cases the automatic run does not cover: the engine was down when the
     * vitals were taken, and a patient who has been waiting long enough that their
     * observations are stale. It does NOT detect deterioration — nothing here can. A
     * score is a function of the observations, so re-running it on unchanged observations
     * returns the same answer. Only new measurements change a priority.
     */
    public function retriage(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        abort_if(
            $entry->vitals_taken_at === null,
            422,
            'This patient has no recorded observations yet, so there is nothing to score.',
        );

        $result = TriageRunner::run($entry);

        abort_if(
            $result === null,
            503,
            'The triage engine is not reachable. The patient is unchanged and still unscored.',
        );

        Auditor::record($request->user()->id, 'waiting_room.retriaged', $entry, null, [
            'priority' => $result['priority'],
            'request_id' => $result['request_id'],
        ], $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /**
     * The nurse's own ordering, recorded beside the agent's.
     *
     * The agent's answer is never overwritten — `suggested_priority` keeps what it said,
     * and both travel to the front end so the disagreement is visible. A reason is
     * required, because an override with no reason is indistinguishable from a misclick
     * and is worthless when someone asks later why a patient was moved.
     *
     * The rank comes from the engine's ladder, fetched rather than hard-coded, so this
     * controller still does not know what CRITICAL means relative to URGENT.
     */
    public function priority(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $data = $request->validate([
            'priority' => ['required', 'string', 'in:CRITICAL,URGENT,STANDARD,LOW'],
            'reason' => ['required', 'string', 'min:3', 'max:500'],
        ]);

        $rank = $this->rankFor($data['priority']);

        $before = ['nurse_priority' => $entry->nurse_priority_label];

        $entry->forceFill([
            'nurse_priority' => $rank,
            'nurse_priority_label' => $data['priority'],
            'nurse_priority_reason' => $data['reason'],
            'nurse_priority_by' => $request->user()->id,
            'nurse_priority_at' => now(),
        ])->save();

        Auditor::record($request->user()->id, 'waiting_room.priority_override', $entry, $before, [
            'nurse_priority' => $data['priority'],
            'reason' => $data['reason'],
            'agent_priority' => $entry->suggested_priority_label,
        ], $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /** Withdraw an override and go back to the agent's ordering. */
    public function clearPriority(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $entry->forceFill([
            'nurse_priority' => null,
            'nurse_priority_label' => null,
            'nurse_priority_reason' => null,
            'nurse_priority_by' => null,
            'nurse_priority_at' => null,
        ])->save();

        Auditor::record($request->user()->id, 'waiting_room.priority_cleared', $entry, null, null, $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /**
     * The integer for a priority word, from the engine's ladder.
     *
     * Cached for the request only. Asking the engine rather than writing the four values
     * here keeps a single definition of the ordering — the same reason the workflow's
     * transition table is served rather than copied into PHP.
     */
    private function rankFor(string $priority): int
    {
        $ladder = collect(EngineClient::fromConfig()->triageRules()['priorities'] ?? [])
            ->pluck('rank', 'priority');

        abort_if(
            ! $ladder->has($priority),
            422,
            "The engine's rule set does not define a priority called {$priority}.",
        );

        return (int) $ladder->get($priority);
    }

    /**
     * Hand the patient to the doctor.
     *
     * The visit id is attached once the consultation is opened, so the queue row and the
     * encounter can be read together afterwards. It stays nullable until then, because a
     * consultation that gets abandoned is never written.
     */
    public function seen(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $data = $request->validate([
            'visit_id' => ['nullable', 'string'],
        ]);

        // A consultation that has not reached a diagnosis has no row in `visits` yet, so its
        // id cannot go in the constrained column. It is held as a draft pointer instead,
        // and moves across once the visit becomes real.
        $given = $data['visit_id'] ?? null;
        $isRealVisit = $given !== null && Visit::whereKey($given)->exists();

        $entry->update([
            'status' => 'in_consultation',
            'seen_at' => now(),
            'visit_id' => $isRealVisit ? $given : $entry->visit_id,
            'draft_visit_id' => $isRealVisit ? null : ($given ?? $entry->draft_visit_id),
        ]);

        Auditor::record($request->user()->id, 'waiting_room.seen', $entry, null, null, $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /** The patient left without being seen. */
    public function destroy(Request $request, WaitingRoomEntry $entry): JsonResponse
    {
        $entry->update(['status' => 'left']);

        Auditor::record($request->user()->id, 'waiting_room.left', $entry, null, null, $request->ip());

        return response()->json(['entry' => $this->present($entry->fresh())]);
    }

    /**
     * The triage vitals as a starting point for the doctor's findings.
     *
     * Offered, not applied. The doctor's screen prefills from this and the doctor confirms —
     * the consultation record must say what the doctor observed, not what was copied into it.
     */
    public function forConsultation(Request $request, Patient $patient): JsonResponse
    {
        $visitId = $request->string('visit_id')->value() ?: null;

        $entry = self::currentAttendance($patient->id, $visitId);

        return response()->json([
            'entry_id' => $entry?->id,
            'vitals' => $entry ? $this->vitalsOf($entry) : null,
            'taken_at' => $entry?->vitals_taken_at,
            // Whether anything has actually been measured this time round. Without it the
            // doctor cannot tell "all normal" from "the nurse has not been yet".
            'recorded' => $entry?->vitals_taken_at !== null,
        ]);
    }

    /**
     * The attendance the patient is on *now*.
     *
     * Vitals belong to a visit to the clinic, not to the person. Someone who comes back a
     * week later is measured again, and the readings from last time must not be offered as
     * though they were today's — a saturation of 96% from a previous illness prefilled into
     * a new consultation is worse than an empty field, because it looks like a measurement.
     *
     * So this only ever looks at an open attendance, and prefers the one already tied to
     * the visit being worked on. A closed entry is history and is never returned.
     */
    public static function currentAttendance(string $patientId, ?string $visitId = null): ?WaitingRoomEntry
    {
        $open = ['waiting', 'in_consultation'];

        if ($visitId) {
            $linked = WaitingRoomEntry::where('patient_id', $patientId)
                ->where(fn ($q) => $q->where('visit_id', $visitId)->orWhere('draft_visit_id', $visitId))
                ->whereIn('status', $open)
                ->orderByDesc('arrived_at')
                ->first();

            if ($linked) {
                return $linked;
            }
        }

        // Not linked yet — the doctor may be opening the consultation right now.
        return WaitingRoomEntry::where('patient_id', $patientId)
            ->whereIn('status', $open)
            ->orderByDesc('arrived_at')
            ->first();
    }

    /**
     * Close the attendance when the encounter ends.
     *
     * This is what makes the reset real. Without it an `in_consultation` row lives forever,
     * and every future consultation for that patient is offered vitals measured on some
     * earlier day.
     */
    public static function closeFor(string $visitId): void
    {
        WaitingRoomEntry::where(fn ($q) => $q->where('visit_id', $visitId)->orWhere('draft_visit_id', $visitId))
            ->whereIn('status', ['waiting', 'in_consultation'])
            ->update(['status' => 'completed']);
    }

    /** Only the fields the engine's Vitals model knows about; height is ours alone. */
    private function vitalsOf(WaitingRoomEntry $entry): array
    {
        return [
            'temperature_c' => $entry->temperature_c !== null ? (float) $entry->temperature_c : null,
            'heart_rate' => $entry->heart_rate,
            'respiratory_rate' => $entry->respiratory_rate,
            'blood_pressure' => $entry->blood_pressure,
            'spo2' => $entry->spo2 !== null ? (float) $entry->spo2 : null,
            'weight_kg' => $entry->weight_kg !== null ? (float) $entry->weight_kg : null,
        ];
    }
}
