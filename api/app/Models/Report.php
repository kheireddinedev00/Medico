<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * An uploaded report, and what has been done with it.
 *
 * `extracted` is written once, at upload, and never modified. There is no setter here and
 * there should be no update path anywhere in the application — it is the transcription, and
 * its value is that it still says what the page said.
 *
 * `analysis` is filled in later, by an explicit physician action. Until then it is null, and
 * null means something specific: uploaded, but nobody has asked what it means. The engine
 * refuses to reason over an unanalysed report for exactly that reason, so this is a real
 * clinical state rather than a loading placeholder.
 */
class Report extends Model
{
    protected $keyType = 'string';
    public $incrementing = false;

    protected $fillable = [
        'id', 'patient_id', 'visit_id', 'investigation_id', 'kind', 'report_type', 'report_date',
        'source_file', 'extracted', 'analysis', 'analysed_at',
        'uploaded_by', 'analysed_by', 'uploaded_at', 'included_in_results',
    ];

    protected $casts = [
        'extracted' => 'array',
        'analysis' => 'array',
        'analysed_at' => 'datetime',
        'uploaded_at' => 'datetime',
        'included_in_results' => 'boolean',
    ];

    public function patient(): BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }

    public function visit(): BelongsTo
    {
        return $this->belongsTo(Visit::class);
    }

    /** The test this report answers, when it was uploaded against one. */
    public function investigation(): BelongsTo
    {
        return $this->belongsTo(Investigation::class);
    }

    public function results(): HasMany
    {
        return $this->hasMany(ReportResult::class);
    }

    public function isAnalysed(): bool
    {
        return $this->analysis !== null;
    }

    /** As it appears in a list: what it is, and when it was taken. */
    public function label(): string
    {
        $parts = array_filter([$this->report_type ?: $this->kind, $this->report_date]);

        return implode(' — ', $parts);
    }

    /** Serialise to the engine's StoredReport. */
    public function toEngineReport(): array
    {
        return [
            'id' => $this->id,
            'patient_id' => $this->patient_id,
            'visit_id' => $this->visit_id,
            'kind' => $this->kind ?? 'laboratory',
            'report_type' => $this->report_type,
            'report_date' => $this->report_date,
            'source_file' => $this->source_file,
            'uploaded_at' => $this->uploaded_at?->toIso8601String(),
            'extracted' => $this->extracted ?? [],
            'analysis' => $this->analysis,
            'analysed_at' => $this->analysed_at?->toIso8601String(),
        ];
    }
}
