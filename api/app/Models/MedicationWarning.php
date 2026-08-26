<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * Withheld drugs are stored, not merely dropped. A drug suppressed for an allergy that
 * leaves no trace is indistinguishable from one the model never suggested, and being able
 * to show the difference is the feature.
 */
class MedicationWarning extends Model
{
    protected $fillable = ['assistant_run_id', 'drug', 'severity', 'reason'];

    public function run(): BelongsTo
    {
        return $this->belongsTo(AssistantRun::class, 'assistant_run_id');
    }
}
