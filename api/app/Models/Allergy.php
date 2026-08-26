<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * `substance` stays exactly as the doctor typed it. Mapping it onto a drug class happens in
 * the engine's safety screen, at screening time — normalising it here would rewrite the
 * record to suit the matcher.
 */
class Allergy extends Model
{
    protected $fillable = ['patient_id', 'substance', 'reaction', 'severity'];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }
}
