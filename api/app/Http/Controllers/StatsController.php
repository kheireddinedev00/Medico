<?php

namespace App\Http\Controllers;

use App\Models\Patient;
use App\Models\ReferenceDocument;
use App\Models\Report;
use App\Models\User;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

/**
 * Numbers for the dashboards.
 *
 * One route, three answers, because "the dashboard" means something different to each role.
 * A doctor opening the application wants to know who is waiting for *them* and what they
 * left unfinished. A nurse wants the shape of the room. An administrator wants the clinic.
 * Serving one undifferentiated blob and letting the front end pick would leak every
 * doctor's workload to every other doctor, which is the same scoping mistake the waiting
 * room used to make.
 *
 * Everything here is counted from the record rather than cached. The clinic is small — a
 * few hundred rows — and a statistic that can disagree with the table it summarises is
 * worse than a slightly slower page.
 */
class StatsController extends Controller
{
    public function index(Request $request): JsonResponse
    {
        $user = $request->user();

        return response()->json([
            'role' => $user->role,
            'generated_at' => now(),
            'stats' => match ($user->role) {
                User::ROLE_DOCTOR => $this->forDoctor($user),
                User::ROLE_NURSE => $this->forNurse(),
                User::ROLE_ADMIN => $this->forAdmin(),
                default => [],
            },
        ]);
    }

    /**
     * The doctor's own workload. Nobody else's.
     *
     * Scoped to `doctor_id` throughout for the same reason the queue is: in a clinic with
     * several doctors, a count that quietly includes a colleague's patients is worse than
     * no count, because it looks authoritative.
     */
    private function forDoctor(User $doctor): array
    {
        $mine = Visit::where('doctor_id', $doctor->id);

        $queue = WaitingRoomEntry::where('doctor_id', $doctor->id)
            ->whereIn('status', ['waiting', 'in_consultation']);

        return [
            'waiting_for_me' => (clone $queue)->where('status', 'waiting')->count(),
            'in_consultation' => (clone $queue)->where('status', 'in_consultation')->count(),
            // The reason the "In progress" screen exists: visits parked on a laboratory.
            'awaiting_results' => (clone $mine)->where('status', 'WAITING_FOR_TESTS')->count(),
            'open_visits' => (clone $mine)->where('status', '!=', 'COMPLETED')->count(),
            'completed_today' => (clone $mine)->where('status', 'COMPLETED')
                ->whereDate('updated_at', today())->count(),
            'completed_this_week' => (clone $mine)->where('status', 'COMPLETED')
                ->where('updated_at', '>=', now()->subDays(7))->count(),
            'patients_seen' => (clone $mine)->distinct('patient_id')->count('patient_id'),

            // Who is waiting, worst first — the same ordering rule the queue uses, so the
            // dashboard and the waiting room cannot tell different stories.
            'queue_preview' => WaitingRoomEntry::with('patient:id,full_name')
                ->where('doctor_id', $doctor->id)
                ->whereIn('status', ['waiting', 'in_consultation'])
                ->orderByRaw('CASE WHEN COALESCE(nurse_priority, suggested_priority) IS NULL THEN 1 ELSE 0 END')
                ->orderByRaw('COALESCE(nurse_priority, suggested_priority) DESC')
                ->orderBy('arrived_at')
                ->limit(5)
                ->get()
                ->map(fn (WaitingRoomEntry $e) => [
                    'id' => $e->id,
                    'patient' => $e->patient?->full_name,
                    'patient_id' => $e->patient_id,
                    'priority' => $e->nurse_priority_label ?? $e->suggested_priority_label,
                    'status' => $e->status,
                    'waiting_since' => $e->arrived_at,
                    'seen_at' => $e->seen_at,
                ]),

            'recent_visits' => (clone $mine)->with('patient:id,full_name')
                ->orderByDesc('updated_at')->limit(5)->get()
                ->map(fn (Visit $v) => [
                    'id' => $v->id,
                    'patient' => $v->patient?->full_name,
                    'status' => $v->status,
                    'diagnosis' => $v->working_diagnosis_label,
                    'updated_at' => $v->updated_at,
                ]),

            'diagnoses' => (clone $mine)->whereNotNull('working_diagnosis_label')
                ->select('working_diagnosis_label', DB::raw('COUNT(*) as total'))
                ->groupBy('working_diagnosis_label')
                ->orderByDesc('total')->limit(6)->get()
                ->map(fn ($r) => ['label' => $r->working_diagnosis_label, 'count' => (int) $r->total]),
        ];
    }

    /**
     * The shape of the room.
     *
     * The nurse's questions are about the queue as a whole rather than about any one
     * clinician, so nothing here is scoped — every nurse works the same list.
     */
    private function forNurse(): array
    {
        $active = fn () => WaitingRoomEntry::whereIn('status', ['waiting', 'in_consultation']);

        return [
            'waiting' => $active()->where('status', 'waiting')->count(),
            'in_consultation' => $active()->where('status', 'in_consultation')->count(),
            // Two numbers a nurse can actually act on: nobody has measured these people,
            // and nobody has been asked to see those ones.
            'vitals_pending' => $active()->whereNull('temperature_c')->whereNull('spo2')->count(),
            'unassigned' => $active()->whereNull('doctor_id')->count(),
            'arrived_today' => WaitingRoomEntry::whereDate('arrived_at', today())->count(),
            'seen_today' => WaitingRoomEntry::where('status', 'completed')
                ->whereDate('updated_at', today())->count(),
            'registered_today' => Patient::whereDate('created_at', today())->count(),
            'total_patients' => Patient::count(),

            'priorities' => $this->priorityCounts(),
            'by_doctor' => $this->queueByDoctor(),

            'longest_waits' => WaitingRoomEntry::with('patient:id,full_name', 'doctor:id,name')
                ->where('status', 'waiting')
                ->orderBy('arrived_at')->limit(5)->get()
                ->map(fn (WaitingRoomEntry $e) => [
                    'id' => $e->id,
                    'patient' => $e->patient?->full_name,
                    'doctor' => $e->doctor?->name,
                    'priority' => $e->nurse_priority_label ?? $e->suggested_priority_label,
                    'arrived_at' => $e->arrived_at,
                ]),
        ];
    }

    /**
     * The whole clinic, and built to be printed.
     *
     * An administrator's dashboard is a report as much as a screen: it is the thing taken
     * into a meeting. So it carries totals, distributions and a fourteen-day trend rather
     * than only the live figures the clinical roles need.
     */
    private function forAdmin(): array
    {
        return [
            'people' => [
                'doctors' => User::where('role', User::ROLE_DOCTOR)->where('is_active', true)->count(),
                'nurses' => User::where('role', User::ROLE_NURSE)->where('is_active', true)->count(),
                'admins' => User::where('role', User::ROLE_ADMIN)->where('is_active', true)->count(),
                'deactivated' => User::where('is_active', false)->count(),
                'patients' => Patient::count(),
            ],

            'activity' => [
                'visits_total' => Visit::count(),
                'visits_open' => Visit::where('status', '!=', 'COMPLETED')->count(),
                'visits_completed' => Visit::where('status', 'COMPLETED')->count(),
                'visits_today' => Visit::whereDate('created_at', today())->count(),
                'visits_this_week' => Visit::where('created_at', '>=', now()->subDays(7))->count(),
                'waiting_now' => WaitingRoomEntry::whereIn('status', ['waiting', 'in_consultation'])->count(),
                'arrivals_total' => WaitingRoomEntry::count(),
            ],

            'assistant' => [
                'reports_uploaded' => Report::count(),
                'reports_analysed' => Report::whereNotNull('analysis')->count(),
                'reference_documents' => ReferenceDocument::count(),
                'assistant_runs' => DB::table('assistant_runs')->count(),
                'medication_warnings' => DB::table('medication_warnings')->count(),
            ],

            // Where every visit currently stands. The workflow's own vocabulary, not a
            // simplified one — an administrator reading "WAITING_FOR_TESTS" learns
            // something a bucket called "in progress" would have hidden.
            'visits_by_status' => Visit::select('status', DB::raw('COUNT(*) as total'))
                ->groupBy('status')->orderByDesc('total')->get()
                ->map(fn ($r) => ['label' => $r->status, 'count' => (int) $r->total]),

            'visits_by_doctor' => Visit::leftJoin('users', 'users.id', '=', 'visits.doctor_id')
                ->select(DB::raw('COALESCE(users.name, "Unclaimed") as label'), DB::raw('COUNT(*) as total'))
                ->groupBy('label')->orderByDesc('total')->get()
                ->map(fn ($r) => ['label' => $r->label, 'count' => (int) $r->total]),

            'top_diagnoses' => Visit::whereNotNull('working_diagnosis_label')
                ->select('working_diagnosis_label', DB::raw('COUNT(*) as total'))
                ->groupBy('working_diagnosis_label')
                ->orderByDesc('total')->limit(8)->get()
                ->map(fn ($r) => ['label' => $r->working_diagnosis_label, 'count' => (int) $r->total]),

            'priorities' => $this->priorityCounts(),

            'audit_actions' => DB::table('audit_logs')
                ->select('action', DB::raw('COUNT(*) as total'))
                ->groupBy('action')->orderByDesc('total')->limit(10)->get()
                ->map(fn ($r) => ['label' => $r->action, 'count' => (int) $r->total]),

            'trend' => $this->fourteenDayTrend(),

            'staff_workload' => User::where('role', User::ROLE_DOCTOR)
                ->withCount([
                    'visits as total_visits',
                    'visits as open_visits' => fn ($q) => $q->where('status', '!=', 'COMPLETED'),
                ])
                ->orderByDesc('total_visits')->get()
                ->map(fn (User $u) => [
                    'name' => $u->name,
                    'active' => (bool) $u->is_active,
                    'total_visits' => (int) $u->total_visits,
                    'open_visits' => (int) $u->open_visits,
                ]),
        ];
    }

    /** Who is waiting, by priority. Unscored is its own bucket, never folded into LOW. */
    private function priorityCounts(): array
    {
        $labels = ['CRITICAL', 'URGENT', 'STANDARD', 'LOW'];

        $entries = WaitingRoomEntry::whereIn('status', ['waiting', 'in_consultation'])->get();

        $counts = array_fill_keys($labels, 0);
        $counts['NOT SCORED'] = 0;

        foreach ($entries as $entry) {
            $label = $entry->nurse_priority_label ?? $entry->suggested_priority_label;
            $counts[$label && isset($counts[$label]) ? $label : 'NOT SCORED']++;
        }

        return collect($counts)->map(fn ($count, $label) => ['label' => $label, 'count' => $count])
            ->values()->all();
    }

    private function queueByDoctor(): array
    {
        return WaitingRoomEntry::leftJoin('users', 'users.id', '=', 'waiting_room_entries.doctor_id')
            ->whereIn('waiting_room_entries.status', ['waiting', 'in_consultation'])
            ->select(DB::raw('COALESCE(users.name, "Unassigned") as label'), DB::raw('COUNT(*) as total'))
            ->groupBy('label')->orderByDesc('total')->get()
            ->map(fn ($r) => ['label' => $r->label, 'count' => (int) $r->total])
            ->all();
    }

    /**
     * Visits and arrivals per day for a fortnight.
     *
     * Days with nothing in them are filled in as zero rather than skipped. A chart that
     * silently drops empty days compresses a quiet week into a busy-looking line.
     */
    private function fourteenDayTrend(): array
    {
        $from = today()->subDays(13);

        $visits = Visit::where('created_at', '>=', $from)
            ->select(DB::raw('DATE(created_at) as day'), DB::raw('COUNT(*) as total'))
            ->groupBy('day')->pluck('total', 'day');

        $arrivals = WaitingRoomEntry::where('arrived_at', '>=', $from)
            ->select(DB::raw('DATE(arrived_at) as day'), DB::raw('COUNT(*) as total'))
            ->groupBy('day')->pluck('total', 'day');

        $days = [];

        for ($date = $from->copy(); $date <= today(); $date->addDay()) {
            $key = $date->toDateString();
            $days[] = [
                'date' => $key,
                'label' => $date->format('D j M'),
                'visits' => (int) ($visits[$key] ?? 0),
                'arrivals' => (int) ($arrivals[$key] ?? 0),
            ];
        }

        return $days;
    }
}
