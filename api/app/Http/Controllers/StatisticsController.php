<?php

namespace App\Http\Controllers;

use App\Models\Visit;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;

/**
 * How the clinic is actually running.
 *
 * The administrator's dashboard already carries the headline counts — how many patients,
 * how many visits, how many waiting. This is the other question: not how much, but how.
 * Distributions, trends and rates, each computed from the tables rather than stored, so a
 * figure here cannot drift from the record it describes.
 *
 * **Every series carries its own `n`.** With a clinic this size a percentage is easy to
 * overread — "75% of criticals seen within the hour" means something different when it is
 * three patients than when it is three hundred, and the screen says which.
 *
 * Nothing here is patient-identifiable. It is counts, medians and category labels; the one
 * place a person is named is the workload chart, where the name belongs to a member of
 * staff and seeing the distribution of work between them is the entire point.
 */
class StatisticsController extends Controller
{
    /** How far back the daily trend reaches. */
    private const TREND_DAYS = 30;

    public function index(): JsonResponse
    {
        return response()->json([
            'generated_at' => now()->toIso8601String(),
            'arrivals' => $this->arrivalsPerDay(),
            'waiting_outcomes' => $this->waitingOutcomes(),
            'triage_mix' => $this->triageMix(),
            'wait_by_priority' => $this->waitByPriority(),
            'triage_overrides' => $this->triageOverrides(),
            'top_diagnoses' => $this->topDiagnoses(),
            'doctor_workload' => $this->doctorWorkload(),
            'assistant_agreement' => $this->assistantAgreement(),
            'differential_confidence' => $this->differentialConfidence(),
            'investigations' => $this->investigations(),
            'age_and_sex' => $this->ageAndSex(),
            'smoking' => $this->smoking(),
            'assistant_runs' => $this->assistantRuns(),
        ]);
    }

    /**
     * Arrivals per day, every day in the window — including the empty ones.
     *
     * A gap-free axis is the point. Skipping days the clinic saw nobody turns a quiet
     * Friday into a missing bar, and the trend then reads as though it never happened.
     */
    private function arrivalsPerDay(): array
    {
        $from = now()->subDays(self::TREND_DAYS - 1)->startOfDay();

        $counts = DB::table('waiting_room_entries')
            ->selectRaw('DATE(arrived_at) as day, COUNT(*) as n')
            ->where('arrived_at', '>=', $from)
            ->groupBy('day')
            ->pluck('n', 'day');

        $series = [];
        for ($day = $from->copy(); $day <= now(); $day->addDay()) {
            $key = $day->format('Y-m-d');
            $series[] = ['label' => $key, 'value' => (int) ($counts[$key] ?? 0)];
        }

        return ['series' => $series, 'n' => array_sum(array_column($series, 'value'))];
    }

    /**
     * What became of everyone who arrived.
     *
     * `left` is the figure worth watching: someone who came to the clinic and went home
     * without being seen. It is the one outcome here that nobody chose.
     */
    private function waitingOutcomes(): array
    {
        $rows = DB::table('waiting_room_entries')
            ->selectRaw('status, COUNT(*) as n')
            ->groupBy('status')
            ->pluck('n', 'status');

        $labels = [
            'completed' => 'Seen and completed',
            'in_consultation' => 'With a doctor now',
            'waiting' => 'Still waiting',
            'left' => 'Left before being seen',
        ];

        $series = [];
        foreach ($labels as $key => $label) {
            $series[] = ['label' => $label, 'key' => $key, 'value' => (int) ($rows[$key] ?? 0)];
        }

        return ['series' => $series, 'n' => (int) $rows->sum()];
    }

    /**
     * What the triage agent decided, and how many arrivals it never saw.
     *
     * "Not triaged" is a category rather than an omission: it is every arrival recorded
     * before a nurse took observations, and hiding it would make triage coverage look
     * total when it is not.
     */
    private function triageMix(): array
    {
        $rows = DB::table('waiting_room_entries')
            ->selectRaw("COALESCE(suggested_priority_label, 'NOT_TRIAGED') as p, COUNT(*) as n")
            ->groupBy('p')
            ->pluck('n', 'p');

        $order = ['CRITICAL', 'URGENT', 'STANDARD', 'LOW', 'NOT_TRIAGED'];

        $series = [];
        foreach ($order as $key) {
            $series[] = [
                'label' => $key === 'NOT_TRIAGED' ? 'Not triaged' : ucfirst(strtolower($key)),
                'key' => $key,
                'value' => (int) ($rows[$key] ?? 0),
            ];
        }

        return ['series' => $series, 'n' => (int) $rows->sum()];
    }

    /**
     * How long people waited, by how urgent the agent said they were.
     *
     * The median rather than the mean. One patient who arrived on a Friday and was seen on
     * the Monday drags an average across the whole category until it describes nobody; the
     * median keeps saying what a typical wait actually looked like.
     */
    private function waitByPriority(): array
    {
        $rows = DB::table('waiting_room_entries')
            ->selectRaw("COALESCE(suggested_priority_label, 'NOT_TRIAGED') as p, TIMESTAMPDIFF(MINUTE, arrived_at, seen_at) as minutes")
            ->whereNotNull('seen_at')
            ->whereNotNull('arrived_at')
            ->get()
            ->groupBy('p');

        $order = ['CRITICAL', 'URGENT', 'STANDARD', 'LOW', 'NOT_TRIAGED'];

        $series = [];
        foreach ($order as $key) {
            $minutes = collect($rows[$key] ?? [])
                ->pluck('minutes')
                ->filter(fn ($m) => $m !== null && $m >= 0)
                ->sort()
                ->values();

            if ($minutes->isEmpty()) {
                continue;
            }

            $series[] = [
                'label' => $key === 'NOT_TRIAGED' ? 'Not triaged' : ucfirst(strtolower($key)),
                'key' => $key,
                'value' => $this->median($minutes->all()),
                'n' => $minutes->count(),
            ];
        }

        return ['series' => $series, 'n' => array_sum(array_column($series, 'n'))];
    }

    private function median(array $sorted): int
    {
        $count = count($sorted);
        $middle = intdiv($count, 2);

        return (int) round($count % 2
            ? $sorted[$middle]
            : ($sorted[$middle - 1] + $sorted[$middle]) / 2);
    }

    /**
     * How often a nurse disagreed with the agent, and in which direction.
     *
     * The agent may raise a priority but never lower one; a nurse may do either. Both
     * directions are worth seeing: a stream of upward overrides says the rules are too
     * lenient, a stream of downward ones says they are crying wolf.
     */
    private function triageOverrides(): array
    {
        $rank = ['LOW' => 1, 'STANDARD' => 2, 'URGENT' => 3, 'CRITICAL' => 4];

        $rows = DB::table('waiting_room_entries')
            ->select('suggested_priority_label', 'nurse_priority_label')
            ->whereNotNull('nurse_priority_label')
            ->whereNotNull('suggested_priority_label')
            ->get();

        $raised = 0;
        $lowered = 0;
        $agreed = 0;

        foreach ($rows as $row) {
            $was = $rank[$row->suggested_priority_label] ?? 0;
            $now = $rank[$row->nurse_priority_label] ?? 0;

            if ($now > $was) {
                $raised++;
            } elseif ($now < $was) {
                $lowered++;
            } else {
                $agreed++;
            }
        }

        $triaged = DB::table('waiting_room_entries')
            ->whereNotNull('suggested_priority_label')
            ->count();

        return [
            'series' => [
                ['label' => 'A nurse raised it', 'key' => 'raised', 'value' => $raised],
                ['label' => 'A nurse lowered it', 'key' => 'lowered', 'value' => $lowered],
                ['label' => 'Restated at the same level', 'key' => 'agreed', 'value' => $agreed],
                ['label' => 'Left as the agent set it', 'key' => 'untouched', 'value' => max(0, $triaged - $rows->count())],
            ],
            'n' => $triaged,
        ];
    }

    /** The conditions this clinic actually sees, by the diagnosis the physician chose. */
    private function topDiagnoses(): array
    {
        $rows = DB::table('visits')
            ->selectRaw('working_diagnosis_label as label, COUNT(*) as value')
            ->whereNotNull('working_diagnosis_label')
            ->groupBy('working_diagnosis_label')
            ->orderByDesc('value')
            ->limit(8)
            ->get();

        return [
            'series' => $rows->map(fn ($r) => ['label' => $r->label, 'value' => (int) $r->value])->all(),
            'n' => (int) Visit::whereNotNull('working_diagnosis_label')->count(),
        ];
    }

    /** Visits per doctor. Unassigned visits are shown, not quietly dropped. */
    private function doctorWorkload(): array
    {
        $rows = DB::table('visits')
            ->leftJoin('users', 'users.id', '=', 'visits.doctor_id')
            ->selectRaw("COALESCE(users.name, 'Unassigned') as label, COUNT(*) as value")
            ->groupBy('label')
            ->orderByDesc('value')
            ->get();

        return [
            'series' => $rows->map(fn ($r) => ['label' => $r->label, 'value' => (int) $r->value])->all(),
            'n' => (int) Visit::count(),
        ];
    }

    /**
     * Whether the physician's diagnosis was one the assistant had raised.
     *
     * Matched on the label, because `visit_differentials.icd10_code` is never written — a
     * code comparison would report perfect disagreement and mean nothing by it. The label
     * is what the physician reads and picks from, which makes it the honest comparison.
     *
     * This is not a score for the assistant. "Not on its list" is the row that matters
     * most: it is every time a physician reached a diagnosis the assistant had not thought
     * of, which is the case this whole design exists to leave room for.
     */
    private function assistantAgreement(): array
    {
        $visits = DB::table('visits')
            ->select('id', 'working_diagnosis_label')
            ->whereNotNull('working_diagnosis_label')
            ->get();

        $differentials = DB::table('visit_differentials')
            ->select('visit_id', 'label', 'rank')
            ->get()
            ->groupBy('visit_id');

        $top = 0;
        $lower = 0;
        $none = 0;
        $noAdvice = 0;

        foreach ($visits as $visit) {
            $candidates = $differentials[$visit->id] ?? collect();

            if ($candidates->isEmpty()) {
                $noAdvice++;

                continue;
            }

            $chosen = mb_strtolower(trim($visit->working_diagnosis_label));
            $match = $candidates->first(fn ($c) => mb_strtolower(trim($c->label)) === $chosen);

            if (! $match) {
                $none++;
            } elseif ((int) $match->rank === 1) {
                $top++;
            } else {
                $lower++;
            }
        }

        return [
            'series' => [
                ['label' => 'Its first candidate', 'key' => 'top', 'value' => $top],
                ['label' => 'A lower candidate on its list', 'key' => 'lower', 'value' => $lower],
                ['label' => 'Not on its list at all', 'key' => 'none', 'value' => $none],
                ['label' => 'No differential was requested', 'key' => 'none_asked', 'value' => $noAdvice],
            ],
            'n' => $visits->count(),
        ];
    }

    /** How boldly the assistant ranks what it proposes. */
    private function differentialConfidence(): array
    {
        $order = ['most likely', 'possible', 'less likely', 'unlikely but must be excluded'];

        $rows = DB::table('visit_differentials')
            ->selectRaw('likelihood, COUNT(*) as n')
            ->groupBy('likelihood')
            ->pluck('n', 'likelihood');

        $series = [];
        foreach ($order as $key) {
            $series[] = ['label' => ucfirst($key), 'key' => $key, 'value' => (int) ($rows[$key] ?? 0)];
        }

        return ['series' => $series, 'n' => (int) $rows->sum()];
    }

    /** What gets ordered, and how much of it has come back. */
    private function investigations(): array
    {
        $rows = DB::table('investigations')
            ->selectRaw('category, status, COUNT(*) as n')
            ->groupBy('category', 'status')
            ->get();

        $categories = $rows->pluck('category')->unique()->sort()->values();

        $series = $categories->map(function ($category) use ($rows) {
            $for = fn ($status) => (int) ($rows->first(
                fn ($r) => $r->category === $category && $r->status === $status
            )->n ?? 0);

            return [
                'label' => ucfirst((string) $category),
                'parts' => ['resulted' => $for('resulted'), 'ordered' => $for('ordered')],
            ];
        })->all();

        return [
            'stacks' => ['resulted' => 'Resulted', 'ordered' => 'Still awaited'],
            'series' => $series,
            'n' => (int) $rows->sum('n'),
        ];
    }

    /**
     * Who the clinic sees, by age band and sex.
     *
     * Age comes from the date of birth where there is one and the recorded age where there
     * is not — both are in use, and preferring one silently would drop part of the register
     * off the chart. Patients with neither are counted separately rather than binned.
     */
    private function ageAndSex(): array
    {
        $rows = DB::table('patients')
            ->selectRaw('sex, COALESCE(age_years, TIMESTAMPDIFF(YEAR, date_of_birth, CURDATE())) as age')
            ->get();

        $bands = [
            ['label' => '0–17', 'min' => 0, 'max' => 17],
            ['label' => '18–34', 'min' => 18, 'max' => 34],
            ['label' => '35–49', 'min' => 35, 'max' => 49],
            ['label' => '50–64', 'min' => 50, 'max' => 64],
            ['label' => '65+', 'min' => 65, 'max' => 200],
        ];

        $series = [];
        foreach ($bands as $band) {
            $inBand = $rows->filter(
                fn ($r) => $r->age !== null && $r->age >= $band['min'] && $r->age <= $band['max']
            );

            $series[] = [
                'label' => $band['label'],
                'parts' => [
                    'female' => $inBand->where('sex', 'female')->count(),
                    'male' => $inBand->where('sex', 'male')->count(),
                    'unknown' => $inBand->whereNotIn('sex', ['female', 'male'])->count(),
                ],
            ];
        }

        return [
            'stacks' => ['female' => 'Female', 'male' => 'Male', 'unknown' => 'Not recorded'],
            'series' => $series,
            'n' => $rows->filter(fn ($r) => $r->age !== null)->count(),
            'undated' => $rows->filter(fn ($r) => $r->age === null)->count(),
        ];
    }

    /** Smoking status across the register — the exposure that matters most in this clinic. */
    private function smoking(): array
    {
        $rows = DB::table('patients')
            ->selectRaw('smoking_status as s, COUNT(*) as n')
            ->groupBy('s')
            ->pluck('n', 's');

        $labels = [
            'current' => 'Current smoker',
            'former' => 'Former smoker',
            'never' => 'Never smoked',
            'unknown' => 'Not recorded',
        ];

        $series = [];
        foreach ($labels as $key => $label) {
            $series[] = ['label' => $label, 'key' => $key, 'value' => (int) ($rows[$key] ?? 0)];
        }

        return ['series' => $series, 'n' => (int) $rows->sum()];
    }

    /** Which parts of the assistant are actually being used. */
    private function assistantRuns(): array
    {
        $labels = [
            'differential' => 'Differential',
            'icd10' => 'ICD-10 coding',
            'investigations' => 'Investigations',
            'medications' => 'Treatment',
        ];

        $rows = DB::table('assistant_runs')
            ->selectRaw('kind, COUNT(*) as n')
            ->groupBy('kind')
            ->pluck('n', 'kind');

        $series = [];
        foreach ($rows as $kind => $n) {
            $series[] = ['label' => $labels[$kind] ?? ucfirst((string) $kind), 'value' => (int) $n];
        }

        usort($series, fn ($a, $b) => $b['value'] <=> $a['value']);

        return ['series' => $series, 'n' => (int) $rows->sum()];
    }
}
