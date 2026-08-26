<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class PatientMedication extends Model
{
    protected $fillable = [
        'patient_id', 'name', 'dose', 'frequency', 'indication', 'started', 'active',
    ];

    protected $casts = ['active' => 'boolean'];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }
}
