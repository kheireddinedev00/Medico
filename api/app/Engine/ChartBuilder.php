<?php

namespace App\Engine;

use App\Models\Patient;
use App\Models\Visit;

/**
 * Assembles the chart the engine is sent with every request.
 *
 * The engine holds no patient state between calls — that is the property the whole design
 * rests on, and this class is what pays for it. Every request rebuilds the picture from the
 * database, so two consecutive assessments for the same patient are identical in what the
 * model knows. There is no conversation to drift and nothing about a patient left behind in
 * the engine's process afterwards.
 *
 * `history` deliberately carries the patient's whole visit list. The engine filters the
 * current visit out of its own background and decides for itself how many past encounters
 * to summarise (CONTEXT_RECENT_VISITS). Trimming here would move a clinical judgement about
 * what counts as relevant history into this application, where it does not belong.
 */
class ChartBuilder
{
    /** The chart for one visit in progress. */
    public static function forVisit(Visit $visit): array
    {
        $patient = $visit->patient()->with(['allergies', 'medications', 'chronicConditions'])->firstOrFail();

        return [
            'profile' => $patient->toEngineProfile(),
            'visit' => self::loadedVisit($visit)->toEngineVisit(),
            'history' => $patient->visits()
                ->where('id', '!=', $visit->id)
                ->with(['differentials', 'investigations', 'prescriptions', 'reports'])
                ->get()
                ->map(fn (Visit $v) => $v->toEngineVisit())
                ->values()
                ->all(),
            'reports' => $patient->reports()
                ->get()
                ->map(fn ($r) => $r->toEngineReport())
                ->values()
                ->all(),
        ];
    }

    /**
     * The payload for opening a consultation, where no visit exists yet.
     *
     * The engine mints the visit id and returns it with `persist: false`. Nothing is
     * written until the physician commits to a diagnosis.
     */
    public static function forNewVisit(Patient $patient): array
    {
        $patient->loadMissing(['allergies', 'medications', 'chronicConditions']);

        return [
            'profile' => $patient->toEngineProfile(),
            'history' => $patient->visits()
                ->with(['differentials', 'investigations', 'prescriptions', 'reports'])
                ->get()
                ->map(fn (Visit $v) => $v->toEngineVisit())
                ->values()
                ->all(),
            'reports' => $patient->reports()->get()
                ->map(fn ($r) => $r->toEngineReport())
                ->values()
                ->all(),
        ];
    }

    /**
     * A chart around a visit that has not been saved yet.
     *
     * Between "start" and the first decision the visit exists only as an array handed back
     * by the engine, so it cannot be loaded from the database to build the next request.
     * This keeps that in-flight visit usable without writing an abandoned consultation to
     * the record to make the plumbing easier.
     */
    public static function forUnsavedVisit(Patient $patient, array $engineVisit): array
    {
        return array_merge(self::forNewVisit($patient), ['visit' => $engineVisit]);
    }

    private static function loadedVisit(Visit $visit): Visit
    {
        return $visit->loadMissing(['differentials', 'investigations', 'prescriptions', 'reports']);
    }
}
