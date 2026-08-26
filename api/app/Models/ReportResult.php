<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * `value` is a string. Titres ("1:160"), thresholds ("<0.01") and qualitative results
 * ("Negative") are real, and casting them to a number destroys them silently.
 */
class ReportResult extends Model
{
    protected $fillable = [
        'report_id', 'parameter', 'value', 'unit',
        'ref_low', 'ref_high', 'ref_text', 'status', 'flags',
    ];

    public function report(): BelongsTo
    {
        return $this->belongsTo(Report::class);
    }
}
