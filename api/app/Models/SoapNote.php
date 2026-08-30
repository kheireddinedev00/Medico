<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * A note a physician reviewed, edited and saved.
 *
 * Distinct from the generated note, which is a projection of the record and is never
 * stored. This is the version someone read and was willing to sign — their wording, not the
 * system's, and not reproducible from the chart because their edits are not derivable from
 * it.
 *
 * `generated` holds the draft they started from, so the two can always be compared.
 */
class SoapNote extends Model
{
    protected $fillable = [
        'visit_id', 'subjective', 'objective', 'assessment', 'plan', 'generated', 'saved_by',
    ];

    protected $casts = ['generated' => 'array'];

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }

    public function author(): BelongsTo
    {
        return $this->belongsTo(User::class, 'saved_by');
    }
}
