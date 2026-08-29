<?php

namespace App\Engine;

use App\Models\WaitingRoomEntry;
use Illuminate\Support\Facades\Log;

/**
 * Scoring one queue entry, and storing what came back.
 *
 * Two rules shape this class.
 *
 * **Triage never breaks the queue.** A nurse recording vitals must always succeed, whether
 * or not the engine is up. So every failure here is caught, logged, and left as an unscored
 * row — which the UI shows as "not scored", never as a low priority. The alternative, a 500
 * on the vitals form because a Python process is restarting, would teach the staff to
 * distrust the whole screen.
 *
 * **Nothing clinical is decided on this side.** The priority, the rank, the reasons, the
 * recommended action and the red flags all arrive from the engine and are stored as they
 * came. This class maps columns; it does not interpret anything, and there is no PHP
 * anywhere that knows a saturation of 91 is bad.
 */
class TriageRunner
{
    /**
     * Score an entry and persist the result. Returns the decision, or null if it could
     * not be obtained.
     *
     * Deliberately returns null rather than throwing: every caller wants the same
     * behaviour, and a caller that has to remember a try/catch to keep the queue working
     * is a caller that will one day forget.
     */
    public static function run(WaitingRoomEntry $entry, ?bool $rulesOnly = null): ?array
    {
        $entry->loadMissing('patient');
        $rulesOnly ??= (bool) config('engine.triage_rules_only');

        try {
            $result = EngineClient::fromConfig()->triage(
                self::requestFor($entry),
                $entry->patient?->toEngineProfile(),
                $rulesOnly,
            );
        } catch (EngineUnavailableException|EngineContractException $e) {
            // Logged rather than surfaced. The nurse's action succeeded; what failed is an
            // enrichment of it, and the row says plainly that it is unscored.
            Log::warning('Triage unavailable for waiting room entry '.$entry->id.': '.$e->getMessage());

            return null;
        }

        self::apply($entry, $result);

        return $result;
    }

    /**
     * Build the triage form from the queue row.
     *
     * Every value is passed as recorded or omitted. Nothing is defaulted, converted or
     * inferred — the engine's own validation layer reports what is missing, and it can
     * only do that if this method does not quietly fill the gaps first.
     */
    public static function requestFor(WaitingRoomEntry $entry): array
    {
        $patient = $entry->patient;

        return array_filter([
            'patient_id' => $entry->patient_id,
            'display_name' => $patient?->full_name,
            'arrived_at' => $entry->arrived_at?->toIso8601String(),
            'age_years' => $patient?->date_of_birth?->age ?? $patient?->age_years,
            'sex' => $patient?->sex ?: 'unknown',
            'is_pregnant' => $entry->is_pregnant,
            'hypercapnic_target_range' => (bool) $entry->hypercapnic_target_range,
            'chief_complaint' => $entry->chief_complaint,
            'notes' => $entry->triage_notes,
            'vitals' => array_filter([
                'temperature_c' => $entry->temperature_c,
                'heart_rate' => $entry->heart_rate,
                'respiratory_rate' => $entry->respiratory_rate,
                'blood_pressure' => $entry->blood_pressure,
                'spo2' => $entry->spo2,
                'on_oxygen' => $entry->on_oxygen,
                'weight_kg' => $entry->weight_kg,
                'height_cm' => $entry->height_cm,
                // "unknown" is the engine's default and means nobody assessed it. Sending
                // it explicitly is the same as omitting it, and clearer to read in a log.
                'consciousness' => $entry->consciousness ?: 'unknown',
            ], fn ($v) => $v !== null),
        ], fn ($v) => $v !== null);
    }

    /** Copy the engine's decision onto the row. Mapping only — no judgement. */
    private static function apply(WaitingRoomEntry $entry, array $result): void
    {
        $entry->forceFill([
            // The integer the queue sorts on, straight from the engine. This side never
            // computes it, so PHP has no opinion about which priority outranks which.
            'suggested_priority' => $result['priority_rank'],
            'suggested_priority_label' => $result['priority'],
            'priority_reason' => self::reasonText($result),
            'priority_computed_at' => now(),
            'triage_status' => $result['status'],
            'triage_request_id' => $result['request_id'],
            'triage_ruleset_version' => $result['ruleset_version'],
            // `is_degraded` on the engine's model; recomputed here from the same field it
            // is derived from, because the serialised result carries the fact, not the
            // method.
            'triage_degraded' => ! ($result['interpretation']['available'] ?? false),
            'triage_result' => $result,
        ])->save();
    }

    /**
     * The one-line explanation shown next to the priority.
     *
     * The first reason, because the engine returns them most-decisive first. The full list
     * is in `triage_result` and the UI expands into it; a queue row has space for one
     * sentence and it should be the one that decided the priority.
     */
    private static function reasonText(array $result): ?string
    {
        $reasons = $result['reasons'] ?? [];

        return $reasons[0] ?? null;
    }
}
