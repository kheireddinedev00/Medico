<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * The nurse's queue. Vitals belong to the visit; this row is about who is waiting.
 *
 * The `suggested_priority` / `nurse_priority` pair is reserved for the triage agent, which
 * is not built yet. They stay separate columns for the same reason the assistant's
 * differential is never merged into the physician's working diagnosis.
 */
class WaitingRoomEntry extends Model
{
    /** waiting | in_consultation | completed | left */
    protected $fillable = [
        'patient_id', 'visit_id', 'draft_visit_id', 'nurse_id', 'arrived_at', 'seen_at', 'status',
        // Triage vitals. Omitting these from the list does not raise an error — Eloquent
        // drops non-fillable attributes silently — so a missing name here reads as a
        // measurement the nurse never took.
        'temperature_c', 'heart_rate', 'respiratory_rate', 'blood_pressure',
        'spo2', 'weight_kg', 'height_cm', 'vitals_taken_at',
        'suggested_priority', 'priority_reason', 'priority_computed_at', 'nurse_priority',
    ];

    protected $casts = [
        'arrived_at' => 'datetime',
        'seen_at' => 'datetime',
        'vitals_taken_at' => 'datetime',
        'priority_computed_at' => 'datetime',
        'temperature_c' => 'float',
        'spo2' => 'float',
        'weight_kg' => 'float',
        'height_cm' => 'float',
    ];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }
}
