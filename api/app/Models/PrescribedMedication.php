<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class PrescribedMedication extends Model
{
    protected $fillable = ['visit_id', 'name', 'dose', 'frequency', 'duration', 'rationale'];

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }
}
