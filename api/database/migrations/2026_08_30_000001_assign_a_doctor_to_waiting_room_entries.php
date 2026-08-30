<?php

/**
 * Which doctor a waiting patient is for.
 *
 * The queue is shared: every nurse works the same list, and any of them can add to it. What
 * is *not* shared is a doctor's view of it — a doctor sees the patients assigned to them,
 * not the whole clinic. A room with four doctors in it and one undivided list is a room
 * where two of them open the same patient.
 *
 * Nullable on purpose, and it means something: nobody has been assigned yet. A nurse
 * registers an arrival before knowing who will see them, and the queue has to be able to
 * hold that honestly rather than defaulting someone in. Unassigned patients stay visible to
 * the nurses, who are the ones who can fix it.
 *
 * `nullOnDelete` rather than cascade: a doctor's account going away must not delete the
 * patients who were waiting for them. The row survives, unassigned, and a nurse reassigns.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->foreignId('doctor_id')->nullable()->after('nurse_id')
                ->constrained('users')->nullOnDelete();

            // Who last changed the assignment, and when. A patient moved between doctors
            // is a thing people ask about afterwards ("why did I get this one?"), and the
            // audit log answers it in full — this is just what the queue itself can show.
            $table->foreignId('assigned_by')->nullable()->after('doctor_id')
                ->constrained('users')->nullOnDelete();
            $table->timestamp('assigned_at')->nullable()->after('assigned_by');

            $table->index(['doctor_id', 'status']);
        });

        /*
         * Rows that predate the column are not unassigned — they are unrecorded. A patient
         * who was already with a doctor when this ran would otherwise land in nobody's
         * queue: invisible to every doctor, and refused to the nurse because the entry is
         * mid-consultation. The visit already knows who is seeing them, so take it from
         * there rather than leaving a row nobody can reach.
         */
        DB::statement(<<<'SQL'
            UPDATE waiting_room_entries
               SET doctor_id = (SELECT doctor_id FROM visits WHERE visits.id = waiting_room_entries.visit_id)
             WHERE doctor_id IS NULL
               AND visit_id IS NOT NULL
        SQL);
    }

    public function down(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->dropIndex(['doctor_id', 'status']);
            $table->dropConstrainedForeignId('assigned_by');
            $table->dropConstrainedForeignId('doctor_id');
            $table->dropColumn('assigned_at');
        });
    }
};
