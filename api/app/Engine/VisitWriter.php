<?php

namespace App\Engine;

use App\Models\Patient;
use App\Models\Visit;
use Illuminate\Support\Facades\DB;

/**
 * Writes an engine response back into the record.
 *
 * This is the only class that persists a visit, and it does exactly one thing: it takes the
 * visit the engine returned and makes the database say the same. It evaluates nothing. It
 * does not decide whether a transition was legal — the engine already refused if it was
 * not — and it does not decide whether the visit should be stored, because the engine
 * answered that too.
 *
 * `persist` is the flag that matters. False means the physician has not committed to a
 * diagnosis, so this consultation is not part of the record; a doctor who opens a patient,
 * reads the assessment and closes the tab must leave nothing behind. Honouring that here is
 * what keeps the rule in one place instead of two.
 */
class VisitWriter
{
    /**
     * @param  array  $result  the engine's ['visit' => [...], 'persist' => bool]
     * @return Visit|null  the saved visit, or null when the engine said not to save
     */
    public static function apply(Patient $patient, array $result): ?Visit
    {
        if (! ($result['persist'] ?? false)) {
            return null;
        }

        $data = $result['visit'];

        return DB::transaction(function () use ($patient, $data) {
            $visit = Visit::findOrNew($data['id']);
            $visit->id = $data['id'];
            $visit->patient_id = $patient->id;
            $visit->status = $data['status'];

            $visit->chief_complaint = $data['chief_complaint'] ?? null;
            $visit->symptoms = $data['symptoms'] ?? [];
            $visit->physical_exam = $data['physical_exam'] ?? null;
            $visit->observations = $data['observations'] ?? null;
            $visit->results_summary = $data['results_summary'] ?? null;
            $visit->doctor_notes = $data['doctor_notes'] ?? null;

            $vitals = $data['vitals'] ?? [];
            foreach (['temperature_c', 'heart_rate', 'respiratory_rate', 'blood_pressure', 'spo2', 'weight_kg'] as $field) {
                $visit->{$field} = $vitals[$field] ?? null;
            }

            // The physician's decision, flattened onto the visit so there can only ever be
            // one of it.
            $diagnosis = $data['working_diagnosis'] ?? null;
            $visit->working_diagnosis_label = $diagnosis['label'] ?? null;
            $visit->working_diagnosis_icd10 = $diagnosis['icd10_code'] ?? null;
            $visit->working_diagnosis_reasoning = $diagnosis['reasoning'] ?? null;

            $visit->save();

            // The engine says which reports it took into the results. Persist that, or the
            // next round trip loses it and the same result is offered for adding again.
            $included = $data['report_ids'] ?? [];
            if ($included) {
                $visit->reports()->whereIn('id', $included)
                    ->update(['included_in_results' => true]);
            }

            self::syncChildren($visit, $data);

            return $visit->fresh([
                'differentials', 'investigations', 'prescriptions', 'reports',
            ]);
        });
    }

    /**
     * Replace the child rows with what the engine returned.
     *
     * Wholesale replacement rather than a diff, because the engine's visit is authoritative
     * after a transition and reconciling row by row would be a second implementation of
     * rules that already exist on the other side. Investigations accumulate in the engine —
     * a second round never erases the first — so what comes back is the full list, and
     * replacing with it preserves that.
     *
     * The trade-off is that child row ids change on every write. Nothing references them,
     * and if anything ever does, this becomes a diff.
     */
    private static function syncChildren(Visit $visit, array $data): void
    {
        $visit->differentials()->delete();
        foreach ($data['differential'] ?? [] as $rank => $diagnosis) {
            $visit->differentials()->create([
                'rank' => $rank,
                'label' => $diagnosis['label'],
                'icd10_code' => $diagnosis['icd10_code'] ?? null,
                // Stays a string. The engine rejects a percentage before it ever gets here.
                'likelihood' => $diagnosis['likelihood'] ?? null,
                'reasoning' => $diagnosis['reasoning'] ?? null,
            ]);
        }

        $visit->investigations()->delete();
        foreach ($data['ordered_investigations'] ?? [] as $investigation) {
            $visit->investigations()->create([
                'name' => $investigation['name'],
                'category' => $investigation['category'] ?? 'other',
                'rationale' => $investigation['rationale'] ?? null,
                'status' => $investigation['status'] ?? 'ordered',
            ]);
        }

        $visit->prescriptions()->delete();
        foreach ($data['prescribed_medications'] ?? [] as $medication) {
            $visit->prescriptions()->create([
                'name' => $medication['name'],
                'dose' => $medication['dose'] ?? null,
                'frequency' => $medication['frequency'] ?? null,
                'duration' => $medication['duration'] ?? null,
                'rationale' => $medication['rationale'] ?? null,
            ]);
        }
    }
}
