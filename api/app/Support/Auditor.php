<?php

namespace App\Support;

use App\Models\AuditLog;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Facades\Log;

/**
 * Records who did what.
 *
 * Only human decisions are logged here. What the assistant proposed goes to
 * `assistant_runs`, and keeping the two apart is the same distinction the whole record
 * makes: this table answers "who chose this", and a row in it always names a person.
 *
 * Auditing must never break the clinical action it describes. A failure to write the log is
 * logged and swallowed — losing an audit row is bad, but refusing to record a diagnosis
 * because the audit table is unavailable is worse.
 */
class Auditor
{
    public static function record(
        ?int $userId,
        string $action,
        ?Model $subject = null,
        ?array $before = null,
        ?array $after = null,
        ?string $ip = null,
    ): void {
        try {
            AuditLog::create([
                'user_id' => $userId,
                'action' => $action,
                'subject_type' => $subject ? $subject::class : null,
                'subject_id' => $subject?->getKey(),
                'before' => $before,
                'after' => $after,
                'ip_address' => $ip,
            ]);
        } catch (\Throwable $e) {
            Log::error('Audit write failed', [
                'action' => $action,
                'user_id' => $userId,
                'error' => $e->getMessage(),
            ]);
        }
    }
}
