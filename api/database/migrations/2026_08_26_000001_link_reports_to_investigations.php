<?php

/**
 * Ties an uploaded report to the test it answers.
 *
 * Until now a report belonged to a patient and possibly a visit, which is enough to file it
 * but not enough to close the loop: a visit waiting on three tests with two PDFs attached
 * could not say which two. Linking to the investigation makes the outstanding list real —
 * the doctor can see that the chest X-ray is back and the blood culture is not.
 *
 * Nullable, and nulled rather than cascaded when an investigation goes. A result that
 * arrives for a test nobody ordered is still the patient's result, and deleting the order
 * must never delete the finding.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('reports', function (Blueprint $table) {
            $table->foreignId('investigation_id')->nullable()->after('visit_id')
                ->constrained('investigations')->nullOnDelete();
        });
    }

    public function down(): void
    {
        Schema::table('reports', function (Blueprint $table) {
            $table->dropConstrainedForeignId('investigation_id');
        });
    }
};
