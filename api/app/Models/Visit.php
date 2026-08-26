<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * One encounter.
 *
 * The pair of methods at the bottom is the whole integration. `toEngineVisit()` renders
 * this row into the shape patient/visit.py declares; `Visit` in the engine is the schema of
 * record, and it forbids unknown keys, so `doctor_id` — a column this side needs and the
 * engine has never heard of — must not be sent.
 *
 * Reading the class top to bottom, one thing is worth noticing: `working_diagnosis_*` are
 * columns, so there is exactly one, and `differentials` is a relation, so there can be
 * many. That asymmetry is intentional and clinical. The physician reaches one decision; the
 * assistant offers several suggestions; the record never lets the second become the first.
 */
class Visit extends Model
{
    protected $keyType = 'string';
    public $incrementing = false;

    protected $fillable = [
        'id', 'patient_id', 'status', 'chief_complaint', 'symptoms',
        'physical_exam', 'observations', 'results_summary',
        'temperature_c', 'heart_rate', 'respiratory_rate', 'blood_pressure',
        'spo2', 'weight_kg',
        'working_diagnosis_label', 'working_diagnosis_icd10', 'working_diagnosis_reasoning',
        'doctor_notes', 'doctor_id',
    ];

    protected $casts = [
        'symptoms' => 'array',
        'temperature_c' => 'float',
        'spo2' => 'float',
        'weight_kg' => 'float',
    ];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }

    public function doctor(): BelongsTo
    {
        return $this->belongsTo(User::class, 'doctor_id');
    }

    public function differentials(): HasMany
    {
        return $this->hasMany(VisitDifferential::class)->orderBy('rank');
    }

    public function investigations(): HasMany
    {
        return $this->hasMany(Investigation::class);
    }

    public function prescriptions(): HasMany
    {
        return $this->hasMany(PrescribedMedication::class);
    }

    public function reports(): HasMany
    {
        return $this->hasMany(Report::class);
    }

    public function assistantRuns(): HasMany
    {
        return $this->hasMany(AssistantRun::class);
    }

    /** Whether the encounter is still one the doctor is expected to come back to. */
    public function isOpen(): bool
    {
        return $this->status !== 'COMPLETED';
    }

    /**
     * Serialise to the engine's Visit.
     *
     * Every key here exists in patient/visit.py, and every key there is produced here. If
     * the engine gains a field, this method is the one place that has to learn about it —
     * and until it does, the engine's own validation will say so.
     */
    public function toEngineVisit(): array
    {
        return [
            'id' => $this->id,
            'patient_id' => $this->patient_id,
            'created_at' => $this->created_at?->toIso8601String(),
            'updated_at' => $this->updated_at?->toIso8601String(),
            'status' => $this->status,

            'chief_complaint' => $this->chief_complaint,
            'symptoms' => $this->symptoms ?? [],
            'vitals' => [
                'temperature_c' => $this->temperature_c,
                'heart_rate' => $this->heart_rate,
                'respiratory_rate' => $this->respiratory_rate,
                'blood_pressure' => $this->blood_pressure,
                'spo2' => $this->spo2,
                'weight_kg' => $this->weight_kg,
            ],
            'physical_exam' => $this->physical_exam,
            'observations' => $this->observations,
            'results_summary' => $this->results_summary,

            // The assistant's suggestions.
            'differential' => $this->differentials->map(fn ($d) => [
                'label' => $d->label,
                'icd10_code' => $d->icd10_code,
                'likelihood' => $d->likelihood,
                'reasoning' => $d->reasoning,
            ])->values()->all(),

            // The physician's decision. Null until someone human makes one, which is also
            // what tells the engine this visit is not part of the record yet.
            'working_diagnosis' => $this->working_diagnosis_label ? [
                'label' => $this->working_diagnosis_label,
                'icd10_code' => $this->working_diagnosis_icd10,
                'likelihood' => null,
                'reasoning' => $this->working_diagnosis_reasoning,
            ] : null,

            'ordered_investigations' => $this->investigations->map(fn ($i) => [
                'name' => $i->name,
                'category' => $i->category ?? 'other',
                'rationale' => $i->rationale,
                'status' => $i->status ?? 'ordered',
            ])->values()->all(),

            'prescribed_medications' => $this->prescriptions->map(fn ($p) => [
                'name' => $p->name,
                'dose' => $p->dose,
                'frequency' => $p->frequency,
                'duration' => $p->duration,
                'rationale' => $p->rationale,
            ])->values()->all(),

            'doctor_notes' => $this->doctor_notes,
            // Only the reports whose analysis was folded into this visit. Attached to the
            // visit and included in its results are different facts: sending every attached
            // report here would tell the engine it had already reasoned over a document
            // nobody has added yet.
            'report_ids' => $this->reports->where('included_in_results', true)
                ->pluck('id')->values()->all(),
        ];
    }
}
