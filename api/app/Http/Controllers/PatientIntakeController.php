<?php

namespace App\Http\Controllers;

use App\Models\Allergy;
use App\Models\ChronicCondition;
use App\Models\Patient;
use App\Models\PatientMedication;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * Registering a patient and taking their history.
 *
 * This is the nurse's other job, and it is the only way any of this data enters the system.
 * The assistant cannot write to the record, and the doctor's consultation records the
 * encounter rather than the background — so if an allergy is not captured here, it does not
 * exist, and the safety screen that is supposed to withhold a drug will have nothing to
 * screen against.
 *
 * That is why every list here is editable rather than write-once. A patient who remembers a
 * penicillin reaction halfway through the visit must be able to have it added immediately,
 * and the next assessment must see it.
 */
class PatientIntakeController extends Controller
{
    /**
     * Register a new patient.
     *
     * Ids follow the P-00n pattern the seed data uses so a clinic-issued number and a
     * generated one look the same on a chart.
     */
    public function store(Request $request): JsonResponse
    {
        $data = $request->validate([
            'full_name' => ['required', 'string', 'max:255'],
            'sex' => ['nullable', 'in:male,female,other,unknown'],
            'date_of_birth' => ['nullable', 'date', 'before:today'],
            // The fallback for a patient who knows their age but not their date of birth,
            // which is common. One or the other, never a guessed date.
            'age_years' => ['nullable', 'integer', 'between:0,130'],
            'smoking_status' => ['nullable', 'in:never,former,current,unknown'],
            'pack_years' => ['nullable', 'numeric', 'between:0,200'],
            'quit_year' => ['nullable', 'integer', 'between:1900,2100'],
            'notes' => ['nullable', 'string'],
        ]);

        $patient = Patient::create($data + [
            'id' => $this->nextId(),
            'sex' => $data['sex'] ?? 'unknown',
            // Defaults to "unknown", never to "never". An unasked smoking history and a
            // negative one are different clinical statements, and for a respiratory
            // assistant the difference changes the differential.
            'smoking_status' => $data['smoking_status'] ?? 'unknown',
        ]);

        Auditor::record($request->user()->id, 'patient.registered', $patient, null, $data, $request->ip());

        return response()->json(['patient' => $patient], 201);
    }

    public function update(Request $request, Patient $patient): JsonResponse
    {
        $data = $request->validate([
            'full_name' => ['sometimes', 'string', 'max:255'],
            'sex' => ['nullable', 'in:male,female,other,unknown'],
            'date_of_birth' => ['nullable', 'date', 'before:today'],
            'age_years' => ['nullable', 'integer', 'between:0,130'],
            'smoking_status' => ['nullable', 'in:never,former,current,unknown'],
            'pack_years' => ['nullable', 'numeric', 'between:0,200'],
            'quit_year' => ['nullable', 'integer', 'between:1900,2100'],
            'notes' => ['nullable', 'string'],
        ]);

        $before = $patient->only(array_keys($data));
        $patient->update($data);

        Auditor::record($request->user()->id, 'patient.updated', $patient, $before, $data, $request->ip());

        return response()->json(['patient' => $patient->fresh()]);
    }

    // --- allergies -----------------------------------------------------------------

    /**
     * Record an allergy.
     *
     * `substance` is stored as typed. The engine maps it onto a drug class at screening
     * time — "Sulfa drugs", "Penicillin V" and "Sulfonamides" all match — so normalising it
     * here would only lose what the patient actually said.
     */
    public function addAllergy(Request $request, Patient $patient): JsonResponse
    {
        $data = $request->validate([
            'substance' => ['required', 'string', 'max:255'],
            'reaction' => ['nullable', 'string', 'max:255'],
            'severity' => ['nullable', 'in:mild,moderate,severe,unknown'],
        ]);

        $allergy = $patient->allergies()->create($data + ['severity' => $data['severity'] ?? 'unknown']);

        Auditor::record($request->user()->id, 'patient.allergy_added', $patient, null, $data, $request->ip());

        return response()->json(['allergy' => $allergy], 201);
    }

    public function removeAllergy(Request $request, Patient $patient, Allergy $allergy): JsonResponse
    {
        abort_if($allergy->patient_id !== $patient->id, 404);

        // Recorded before deletion. Removing an allergy is a consequential act — it is what
        // stands between this patient and a drug they react to — so the audit row has to
        // carry what was there.
        Auditor::record($request->user()->id, 'patient.allergy_removed', $patient, $allergy->toArray(), null, $request->ip());
        $allergy->delete();

        return response()->json(['removed' => true]);
    }

    // --- medications ---------------------------------------------------------------

    public function addMedication(Request $request, Patient $patient): JsonResponse
    {
        $data = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'dose' => ['nullable', 'string', 'max:120'],
            'frequency' => ['nullable', 'string', 'max:120'],
            'indication' => ['nullable', 'string', 'max:255'],
            // Free text: "2019", "6 months ago", "since childhood" are all real answers.
            'started' => ['nullable', 'string', 'max:120'],
            'active' => ['nullable', 'boolean'],
        ]);

        $medication = $patient->medications()->create($data + ['active' => $data['active'] ?? true]);

        Auditor::record($request->user()->id, 'patient.medication_added', $patient, null, $data, $request->ip());

        return response()->json(['medication' => $medication], 201);
    }

    /**
     * Stop a medication rather than delete it.
     *
     * What someone used to take is clinical history, not a mistake to erase — it explains
     * why a drug was changed and matters to the next prescriber. Deletion is available, but
     * this is the one the interface should offer.
     */
    public function stopMedication(Request $request, Patient $patient, PatientMedication $medication): JsonResponse
    {
        abort_if($medication->patient_id !== $patient->id, 404);

        $medication->update(['active' => false]);
        Auditor::record($request->user()->id, 'patient.medication_stopped', $patient, null, ['name' => $medication->name], $request->ip());

        return response()->json(['medication' => $medication->fresh()]);
    }

    public function removeMedication(Request $request, Patient $patient, PatientMedication $medication): JsonResponse
    {
        abort_if($medication->patient_id !== $patient->id, 404);

        Auditor::record($request->user()->id, 'patient.medication_removed', $patient, $medication->toArray(), null, $request->ip());
        $medication->delete();

        return response()->json(['removed' => true]);
    }

    // --- chronic conditions ---------------------------------------------------------

    public function addCondition(Request $request, Patient $patient): JsonResponse
    {
        $data = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'since' => ['nullable', 'string', 'max:120'],
            'notes' => ['nullable', 'string'],
        ]);

        $condition = $patient->chronicConditions()->create($data);

        Auditor::record($request->user()->id, 'patient.condition_added', $patient, null, $data, $request->ip());

        return response()->json(['condition' => $condition], 201);
    }

    public function removeCondition(Request $request, Patient $patient, ChronicCondition $condition): JsonResponse
    {
        abort_if($condition->patient_id !== $patient->id, 404);

        Auditor::record($request->user()->id, 'patient.condition_removed', $patient, $condition->toArray(), null, $request->ip());
        $condition->delete();

        return response()->json(['removed' => true]);
    }

    /**
     * The next P-00n, continuing from the highest already in use.
     *
     * Locked for the duration so two nurses registering at once cannot be handed the same
     * id — a collision here would attach one patient's history to another's chart.
     */
    private function nextId(): string
    {
        return DB::transaction(function () {
            $highest = Patient::query()
                ->where('id', 'like', 'P-%')
                ->lockForUpdate()
                ->pluck('id')
                ->map(fn ($id) => (int) substr($id, 2))
                ->max() ?? 0;

            return sprintf('P-%03d', $highest + 1);
        });
    }
}
