<?php

/**
 * Remembers the consultation a doctor is part-way through, before it is a record.
 *
 * `visit_id` carries a foreign key, which is correct — it points at a real encounter. But a
 * consultation that has not reached a diagnosis has no row in `visits` yet, by design: the
 * engine refuses to persist one, so a doctor who walks away leaves nothing behind. That id
 * cannot go in a column constrained to a table it is deliberately absent from.
 *
 * So it gets its own column, deliberately without a constraint. It holds a pointer to
 * something in flight, and it is nulled the moment the visit becomes real.
 *
 * Without it, a doctor who steps back to check a chart and returns is handed a *second*
 * empty consultation, with the first one's findings stranded in a draft nobody can reach.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->string('draft_visit_id')->nullable()->after('visit_id');
        });
    }

    public function down(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->dropColumn('draft_visit_id');
        });
    }
};
