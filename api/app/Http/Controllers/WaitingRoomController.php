<?php

namespace App\Http\Controllers;

use App\Models\Patient;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * The nurse's queue.
 *
 * Nothing here reasons clinically and nothing here calls the engine. A patient arrives, a
 * nurse measures them, the doctor takes them through. That is the whole of it.
 *
 * The triage agent will attach to this controller later — it will fill `suggested_priority`
 * and `priority_reason`, and the nurse's own ordering will stay in `nurse_priority`
 * alongside. Until it exists, the queue is ordered by arrival time, which is the honest
 * default: a system that invents a priority it cannot justify is worse than one that admits
 * it sorts by who came first.
 */
class WaitingRoomController extends Controller
{
    /** The current queue, longest wait first. */
    public function index(Request $request): JsonResponse
    {
        $status = $request->string('status')->value() ?: 'active';

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
            ->orderBy('arrived_at')
            ->get();

        return response()->json([
            'entries' => $entries->map(fn (WaitingRoomEntry $e) => $this->present($e)),
        ]);
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

            // Populated by the triage agent when it exists. Null means "not scored", which
            // the UI must show as such rather than as a low priority.
            'suggested_priority' => $entry->suggested_priority,
            'priority_reason' => $entry->priority_reason,
            'nurse_priority' => $entry->nurse_priority,
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
        ]);

        $before = $this->vitalsOf($entry);

        $entry->fill($data);
        $entry->vitals_taken_at = now();
        $entry->nurse_id = $request->user()->id;
        $entry->save();

        Auditor::record($request->user()->id, 'waiting_room.vitals', $entry, $before, $this->vitalsOf($entry), $request->ip());

        return response()->json([
            'entry' => $this->present($entry->fresh()),
            'vitals' => $this->vitalsOf($entry),
        ]);
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
