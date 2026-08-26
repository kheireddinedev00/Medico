<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * The permanent patient record.
 *
 * `toEngineProfile()` is the important method in this class, and the reason the normalised
 * child tables above it are allowed to exist at all. Laravel stores allergies and
 * medications in rows because forms and queries want rows; the engine expects the exact
 * shape declared in patient/profile.py. This method is the one place that conversion
 * happens.
 *
 * It has to be exact. `PatientProfile` is declared with `extra="forbid"`, so an added key —
 * a Laravel timestamp, an id on a child row — is rejected by the engine rather than
 * ignored. That strictness is a feature: it means a mismatch surfaces as a 422 during
 * development instead of as a dropped allergy in front of a patient.
 */
class Patient extends Model
{
    protected $keyType = 'string';
    public $incrementing = false;

    protected $fillable = [
        'id', 'full_name', 'sex', 'date_of_birth', 'age_years',
        'smoking_status', 'pack_years', 'quit_year', 'notes',
    ];

    protected $casts = [
        'date_of_birth' => 'date',
        'pack_years' => 'float',
    ];

    public function allergies(): HasMany
    {
        return $this->hasMany(Allergy::class);
    }

    public function medications(): HasMany
    {
        return $this->hasMany(PatientMedication::class);
    }

    public function chronicConditions(): HasMany
    {
        return $this->hasMany(ChronicCondition::class);
    }

    public function visits(): HasMany
    {
        return $this->hasMany(Visit::class);
    }

    public function reports(): HasMany
    {
        return $this->hasMany(Report::class);
    }

    /**
     * Serialise to the engine's PatientProfile.
     *
     * Note what is absent: there is no `age`. The engine computes it from date_of_birth,
     * because a stored age is wrong the day after a birthday and age drives real clinical
     * thresholds. `age_years` is only the fallback for a record that never had a date.
     */
    public function toEngineProfile(): array
    {
        return [
            'id' => $this->id,
            'full_name' => $this->full_name,
            'sex' => $this->sex ?? 'unknown',
            'date_of_birth' => $this->date_of_birth?->format('Y-m-d'),
            'age_years' => $this->age_years,
            'allergies' => $this->allergies->map(fn ($a) => [
                'substance' => $a->substance,
                'reaction' => $a->reaction,
                'severity' => $a->severity ?? 'unknown',
            ])->values()->all(),
            'medications' => $this->medications->map(fn ($m) => [
                'name' => $m->name,
                'dose' => $m->dose,
                'frequency' => $m->frequency,
                'indication' => $m->indication,
                'started' => $m->started,
                'active' => (bool) $m->active,
            ])->values()->all(),
            'chronic_conditions' => $this->chronicConditions->map(fn ($c) => [
                'name' => $c->name,
                'since' => $c->since,
                'notes' => $c->notes,
            ])->values()->all(),
            'smoking' => [
                'status' => $this->smoking_status ?? 'unknown',
                'pack_years' => $this->pack_years !== null ? (float) $this->pack_years : null,
                'quit_year' => $this->quit_year,
            ],
            'notes' => $this->notes,
        ];
    }
}
