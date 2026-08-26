<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * What the assistant proposed. Never what was decided — that lives on the visit itself.
 *
 * `likelihood` is a string and must stay one. The engine rejects a percentage outright, and
 * nothing on this side should quietly make one storable again.
 */
class VisitDifferential extends Model
{
    protected $fillable = [
        'visit_id', 'rank', 'label', 'icd10_code', 'likelihood', 'reasoning', 'citations',
    ];

    protected $casts = ['citations' => 'array'];

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }
}
