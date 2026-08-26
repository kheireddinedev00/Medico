<?php

/**
 * Which reports have actually been brought into a visit's results.
 *
 * `Visit.report_ids` in the engine means something precise: the reports whose analysis the
 * physician folded into this encounter, so the assistant reasons over them when it
 * re-assesses. It is written by `record_results_from_reports` and by nothing else.
 *
 * Laravel was deriving that list from the reports relation — every document filed against
 * the visit, analysed or not, added or not — and sending it back on every round trip. The
 * engine's own meaning was being overwritten with a different one, so a freshly uploaded
 * report looked as though it had already been reasoned over, and the interface stopped
 * offering to add it.
 *
 * Attached to a visit and included in its results are two different facts, so they get two
 * different columns.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('reports', function (Blueprint $table) {
            $table->boolean('included_in_results')->default(false)->after('analysed_at');
        });
    }

    public function down(): void
    {
        Schema::table('reports', function (Blueprint $table) {
            $table->dropColumn('included_in_results');
        });
    }
};
