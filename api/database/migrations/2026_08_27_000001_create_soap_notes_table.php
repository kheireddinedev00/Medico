<?php

/**
 * The note the physician reviewed and saved.
 *
 * This reverses an earlier decision, so the reasoning is worth writing down rather than
 * leaving the reader to wonder.
 *
 * The generated note is still a projection: assembled from the record on request, never
 * stored, so it cannot go stale against the chart it describes. That has not changed, and
 * it is why there is no `soap_notes` row until a human makes one.
 *
 * What is stored here is a different object. Once a physician has read the draft, corrected
 * it and saved it, the result is *their* document — the wording they are willing to put
 * their name to. Regenerating that from the record later would not reproduce it, because
 * their edits are not derivable from anything. A signed note and a live projection are two
 * different artefacts, and only the first belongs in a table.
 *
 * `generated` keeps the projection as it stood when they started editing, so the record can
 * always show what the system proposed against what the clinician actually wrote.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('soap_notes', function (Blueprint $table) {
            $table->id();
            $table->string('visit_id');

            // The four headings, as the physician left them.
            $table->text('subjective')->nullable();
            $table->text('objective')->nullable();
            $table->text('assessment')->nullable();
            $table->text('plan')->nullable();

            // What the system produced before they edited it, kept for comparison.
            $table->json('generated')->nullable();

            $table->foreignId('saved_by')->nullable()->constrained('users')->nullOnDelete();
            $table->timestamps();

            $table->foreign('visit_id')->references('id')->on('visits')->cascadeOnDelete();
            // One saved note per visit. A second would raise the question of which is the
            // note, and a chart with two answers to that is worse than one with none.
            $table->unique('visit_id');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('soap_notes');
    }
};
