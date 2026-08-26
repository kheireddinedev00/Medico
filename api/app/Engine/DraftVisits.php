<?php

namespace App\Engine;

use Illuminate\Support\Facades\Cache;

/**
 * Holds a consultation that has been opened but not yet committed to the record.
 *
 * The engine will not let a visit be stored before the physician chooses a diagnosis, which
 * leaves a real gap: between opening a patient and deciding, the visit exists only as an
 * array. Something has to hold it, and the choice of what matters more than it looks.
 *
 * It is **not** the browser. The visit carries its own `status`, and the engine trusts the
 * status it is given when deciding which transitions are legal — that is correct for a
 * caller it can trust, and this application is that caller. Letting React post the visit
 * back would make the workflow client-controlled: a forged `TREATMENT_SELECTION` would walk
 * straight past diagnosis selection, and the state machine that exists to keep the record
 * honest would be enforcing rules against an attacker's own input.
 *
 * So the draft stays here, keyed by the clinician holding it. The client gets an id and
 * nothing else it can tamper with.
 *
 * Drafts expire. An abandoned consultation should evaporate rather than linger — that is
 * the same rule the `persist` flag encodes, applied to the cache.
 */
class DraftVisits
{
    /** Long enough for an interrupted clinic day, short enough not to be a shadow record. */
    private const TTL_HOURS = 8;

    public static function put(int $userId, array $visit): void
    {
        Cache::put(self::key($userId, $visit['id']), $visit, now()->addHours(self::TTL_HOURS));
    }

    public static function get(int $userId, string $visitId): ?array
    {
        return Cache::get(self::key($userId, $visitId));
    }

    /**
     * Called once the visit has been written to the record. From that moment the database
     * is the only copy, and a stale draft alongside it would be a second answer to "what
     * state is this consultation in".
     */
    public static function forget(int $userId, string $visitId): void
    {
        Cache::forget(self::key($userId, $visitId));
    }

    private static function key(int $userId, string $visitId): string
    {
        return "draft-visit:{$userId}:{$visitId}";
    }
}
