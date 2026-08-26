<?php

/**
 * One visit is one encounter — one row on the patient's timeline.
 *
 * Mirrors patient/visit.py. The shape of these tables carries the project's central
 * claim, so it is worth naming: **what the assistant suggested and what the physician
 * decided are stored apart and never merged.**
 *
 * `visit_differentials` holds the assistant's proposal. The working diagnosis lives in
 * three columns on `visits` and is written only when a human chooses it. The two are
 * allowed to disagree, and the record keeps both without complaint — that disagreement,
 * preserved, is the evidence that the AI is not making the decisions.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('visits', function (Blueprint $table) {
            $table->string('id')->primary();
            $table->string('patient_id');

            // One of the eight states in clinical/consultation_state.py. Not a database
            // enum: the transition table is enforced in Python and served over
            // /reference/workflow, and a copy of the value list here would be a second
            // source of truth for something that already has one.
            $table->string('status')->default('INITIAL_ASSESSMENT');

            // --- what the doctor collected ---
            $table->text('chief_complaint')->nullable();
            $table->json('symptoms')->nullable();
            $table->text('physical_exam')->nullable();
            $table->text('observations')->nullable();
            // Results get their own column rather than being appended to observations.
            // Results decide a differential, and a sentence buried in a paragraph of
            // examination notes does not read as decisive to the model or to a reader.
            $table->text('results_summary')->nullable();

            // --- vitals, recorded as measured; no unit conversion, no derived scores ---
            $table->decimal('temperature_c', 4, 1)->nullable();
            $table->unsignedSmallInteger('heart_rate')->nullable();
            $table->unsignedSmallInteger('respiratory_rate')->nullable();
            // A string, not two integers. "128/76" is how it is written on a chart, and
            // splitting it invites a parse error on real entries like "128/76 (left arm)".
            $table->string('blood_pressure')->nullable();
            $table->decimal('spo2', 4, 1)->nullable();
            $table->decimal('weight_kg', 5, 1)->nullable();

            // --- the physician's decision ---
            $table->string('working_diagnosis_label')->nullable();
            $table->string('working_diagnosis_icd10')->nullable();
            $table->text('working_diagnosis_reasoning')->nullable();
            $table->text('doctor_notes')->nullable();

            // Who saw the patient. Nullable so a visit seeded from historical data does
            // not have to invent a clinician who was never recorded.
            $table->foreignId('doctor_id')->nullable()->constrained('users')->nullOnDelete();

            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            // The timeline query: this patient's visits, newest first.
            $table->index(['patient_id', 'created_at']);
        });

        Schema::create('visit_differentials', function (Blueprint $table) {
            $table->id();
            $table->string('visit_id');
            $table->unsignedTinyInteger('rank')->default(0);
            $table->string('label');
            $table->string('icd10_code')->nullable();
            // TEXT, and a numeric percentage must be rejected before it gets here. The
            // model has no calibrated basis for "70%", but a clinician reads it as if it
            // did. clinical/differential.py rejects one outright; this column must not
            // quietly make it storable again.
            $table->string('likelihood')->nullable();
            $table->text('reasoning')->nullable();
            // Indices into the evidence retrieved for that call, so a citation shown to a
            // physician can be traced back to the excerpt it came from.
            $table->json('citations')->nullable();
            $table->timestamps();

            $table->foreign('visit_id')->references('id')->on('visits')->cascadeOnDelete();
            $table->index(['visit_id', 'rank']);
        });

        // A row exists here only because the physician selected it. `rationale` is the
        // assistant's reason for proposing it, kept because the reason is the part a
        // physician judges — "order a CRP" without one is not decision support.
        Schema::create('investigations', function (Blueprint $table) {
            $table->id();
            $table->string('visit_id');
            $table->string('name');
            $table->string('category')->default('other');   // laboratory | radiology | other
            $table->text('rationale')->nullable();
            $table->string('status')->default('ordered');   // ordered | resulted | cancelled
            $table->timestamps();

            $table->foreign('visit_id')->references('id')->on('visits')->cascadeOnDelete();
            $table->index(['visit_id', 'status']);
        });

        Schema::create('prescribed_medications', function (Blueprint $table) {
            $table->id();
            $table->string('visit_id');
            $table->string('name');
            $table->string('dose')->nullable();
            $table->string('frequency')->nullable();
            $table->string('duration')->nullable();
            $table->text('rationale')->nullable();
            $table->timestamps();

            $table->foreign('visit_id')->references('id')->on('visits')->cascadeOnDelete();
            $table->index('visit_id');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('prescribed_medications');
        Schema::dropIfExists('investigations');
        Schema::dropIfExists('visit_differentials');
        Schema::dropIfExists('visits');
    }
};
