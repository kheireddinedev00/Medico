<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * A reference document a physician added, as this application knows it.
 *
 * The corpus lives in the engine. What is kept here is provenance: who added this, and
 * when. A row existing here is also what makes a document removable — the controller only
 * ever asks the engine to delete a `source_name` it found in this table, so the curated
 * library is not merely protected, it is unaddressable.
 */
class ReferenceDocument extends Model
{
    protected $fillable = [
        'source_name', 'original_filename', 'title', 'publisher', 'year',
        'reference', 'chunks', 'added_by',
    ];

    protected $casts = [
        'year' => 'integer',
        'chunks' => 'integer',
    ];

    /**
     * Routes address a document by the name the engine knows it by, not by row id.
     *
     * The whole system already keys on `source_name`: the vector store stamps it on every
     * chunk, the added-documents registry is keyed by it, and the listing is joined on it.
     * A row id would be a fourth identifier that only this table understands, and the
     * client would have to carry it purely to hand it back.
     */
    public function getRouteKeyName(): string
    {
        return 'source_name';
    }

    public function addedBy(): BelongsTo
    {
        return $this->belongsTo(User::class, 'added_by');
    }
}
