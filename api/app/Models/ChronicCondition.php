<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class ChronicCondition extends Model
{
    protected $fillable = ['patient_id', 'name', 'since', 'notes'];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }
}
