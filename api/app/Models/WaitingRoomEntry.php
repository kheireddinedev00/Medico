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
        // The two NEWS2 parameters that are not numbers, and the presentation itself.
        // Triage sorts on the complaint as much as on the observations.
        'on_oxygen', 'oxygen_delivery', 'consciousness',
        'chief_complaint', 'triage_notes', 'is_pregnant', 'hypercapnic_target_range',
        'suggested_priority', 'priority_reason', 'priority_computed_at', 'nurse_priority',
    ];

    protected $casts = [
        'arrived_at' => 'datetime',
        'seen_at' => 'datetime',
        'vitals_taken_at' => 'datetime',
        'priority_computed_at' => 'datetime',
        'nurse_priority_at' => 'datetime',
        'temperature_c' => 'float',
        'spo2' => 'float',
        'weight_kg' => 'float',
        'height_cm' => 'float',
        // Tri-state. Casting to bool would turn "nobody asked" into "no", and the engine
        // treats those differently on purpose.
        'is_pregnant' => 'boolean',
        'on_oxygen' => 'boolean',
        'hypercapnic_target_range' => 'boolean',
        'triage_degraded' => 'boolean',
        'triage_result' => 'array',
    ];

    /**
     * The rank the queue actually sorts on: the nurse's call when they made one.
     *
     * Null when nobody has scored this patient and no nurse has ordered them by hand.
     * That is a real state — an arrival whose vitals have not been taken — and it sorts
     * last rather than as a low priority, because "not assessed" is not "not urgent".
     */
    public function effectivePriority(): ?int
    {
        return $this->nurse_priority ?? $this->suggested_priority;
    }

    public function effectivePriorityLabel(): ?string
    {
        return $this->nurse_priority !== null
            ? $this->nurse_priority_label
            : $this->suggested_priority_label;
    }

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }
}
