<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

/**
 * Read-only lookup, seeded from the engine's curated list. The description shown to a
 * physician comes from here rather than from the model — the same principle as citations:
 * the model points, the record supplies the authoritative text.
 */
class Icd10Code extends Model
{
    protected $primaryKey = 'code';
    protected $keyType = 'string';
    public $incrementing = false;

    protected $fillable = ['code', 'description', 'synonyms'];

    protected $casts = ['synonyms' => 'array'];
}
