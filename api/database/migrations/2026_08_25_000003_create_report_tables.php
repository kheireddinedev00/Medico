<?php

/**
 * Uploaded reports, and the two things kept about each one.
 *
 * Mirrors patient/report.py, including the order that matters:
 *
 * 1. `extracted` — the transcription, exactly as report_reader produced it. This is the
 *    canonical clinical data. Written once at upload and **never modified**. There is no
 *    application code path that should update this column.
 * 2. `analysis` — the interpretation, produced later by an explicit physician action, and
 *    stored beside the transcription rather than merged into it. A value read off the page
 *    must stay distinguishable from a statement about that value.
 *
 * A report belongs to a patient. It may belong to the visit that ordered it, but it does
 * not have to.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('reports', function (Blueprint $table) {
            $table->string('id')->primary();
            $table->string('patient_id');
            // Nullable, and nulled rather than cascaded when a visit goes: results arrive
            // late, out of order, and sometimes for nothing anyone ordered. A result that
            // outlives the encounter that prompted it is still the patient's result.
            $table->string('visit_id')->nullable();

            $table->string('kind')->default('laboratory');   // laboratory | radiology | other
            // As printed on the report ("CBC", "Chest X-ray"), never inferred.
            $table->string('report_type')->nullable();
            $table->string('report_date')->nullable();
            $table->string('source_file')->nullable();

            $table->json('extracted');
            $table->json('analysis')->nullable();
            $table->timestamp('analysed_at')->nullable();

            // Who uploaded it, and who asked for the interpretation — different people,
            // and at the defence "the analysis was a human decision" needs a column.
            $table->foreignId('uploaded_by')->nullable()->constrained('users')->nullOnDelete();
            $table->foreignId('analysed_by')->nullable()->constrained('users')->nullOnDelete();

            $table->timestamp('uploaded_at')->useCurrent();
            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            $table->foreign('visit_id')->references('id')->on('visits')->nullOnDelete();
            $table->index(['patient_id', 'uploaded_at']);
            $table->index('visit_id');
        });

        // Derived from `extracted` for charting a parameter over time. A convenience, never
        // a substitute — if these disagree with the transcription, the transcription wins.
        Schema::create('report_results', function (Blueprint $table) {
            $table->id();
            $table->string('report_id');
            $table->string('parameter');
            // TEXT, and this is not negotiable. Real results are not always clean numbers:
            // titres ("1:160"), thresholds ("<0.01", ">1000") and qualitative values
            // ("Negative", "Trace", "Yellow") must survive verbatim. A numeric column here
            // would silently corrupt medical data on the way in.
            $table->string('value')->nullable();
            $table->string('unit')->nullable();
            $table->string('ref_low')->nullable();
            $table->string('ref_high')->nullable();
            $table->string('ref_text')->nullable();
            $table->string('status')->nullable();
            // The laboratory's own flag, as printed: "H", "L", "*", "A". A printed flag
            // beats anything computed from the range — the lab knows its assay.
            $table->string('flags')->nullable();
            $table->timestamps();

            $table->foreign('report_id')->references('id')->on('reports')->cascadeOnDelete();
            $table->index(['report_id', 'parameter']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('report_results');
        Schema::dropIfExists('reports');
    }
};
