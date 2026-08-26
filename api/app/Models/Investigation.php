<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * A row exists only because the physician selected it. `rationale` is the assistant's
 * reason for proposing it, which is the part a physician actually judges.
 */
class Investigation extends Model
{
    protected $fillable = ['visit_id', 'name', 'category', 'rationale', 'status'];

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }

    public function reports(): HasMany
    {
        return $this->hasMany(Report::class);
    }
}
