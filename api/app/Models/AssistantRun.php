<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * Append-only. Nothing should update or delete one of these — the point of the table is to
 * answer "what was the model told, and what did it say" months after the consultation.
 */
class AssistantRun extends Model
{
    protected $fillable = [
        'visit_id', 'kind', 'model', 'raw_response', 'evidence', 'requested_by',
    ];

    protected $casts = ['raw_response' => 'array', 'evidence' => 'array'];

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }

    public function warnings(): HasMany
    {
        return $this->hasMany(MedicationWarning::class);
    }
}
