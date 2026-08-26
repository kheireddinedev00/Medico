<?php

/**
 * The permanent patient record — the half of the chart that does not change per visit.
 *
 * Mirrors patient/profile.py. That file is the schema of record: these tables exist to be
 * serialised back into a PatientProfile at the API boundary, and the engine rejects a
 * payload that does not match it exactly (extra="forbid"). When the two disagree, the
 * Pydantic model is right.
 *
 * Status and severity columns are plain strings rather than database enums, deliberately.
 * The permitted values are already declared once, as Literals in the Python models, and a
 * second copy in a migration is a second thing to keep in step — it would drift, and it
 * would drift silently.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('patients', function (Blueprint $table) {
            // String keys, because the engine issues them ("P-001") and the record must
            // be able to hold an id transcribed from an existing paper or clinic system.
            $table->string('id')->primary();
            $table->string('full_name');
            $table->string('sex')->default('unknown');
            $table->date('date_of_birth')->nullable();
            // Fallback for records carrying an age but no date of birth, which is normal
            // when data comes off paper. Age is computed from date_of_birth when present —
            // never stored as a live value, because it is wrong the day after a birthday
            // and age drives real thresholds (CURB-65, paediatric dosing).
            $table->unsignedSmallInteger('age_years')->nullable();

            $table->string('smoking_status')->default('unknown');
            $table->decimal('pack_years', 5, 1)->nullable();
            $table->unsignedSmallInteger('quit_year')->nullable();

            $table->text('notes')->nullable();
            $table->timestamps();

            $table->index('full_name');
        });

        // An empty allergy list means "no known allergies", which is a real clinical
        // statement. It must never be rendered as though it meant "nobody asked" — that
        // distinction is the reason severity defaults to 'unknown' rather than to a value
        // that reads as reassuring.
        Schema::create('allergies', function (Blueprint $table) {
            $table->id();
            $table->string('patient_id');
            // Free text, as the doctor typed it ("Penicillin", "Sulfa drugs"). Mapping a
            // substance onto a drug class is what clinical/medications.py does at screening
            // time; doing it here would silently rewrite what was entered.
            $table->string('substance');
            $table->string('reaction')->nullable();
            $table->string('severity')->default('unknown');
            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            $table->index('patient_id');
        });

        Schema::create('patient_medications', function (Blueprint $table) {
            $table->id();
            $table->string('patient_id');
            $table->string('name');
            $table->string('dose')->nullable();
            $table->string('frequency')->nullable();
            $table->string('indication')->nullable();
            // Free text on purpose: "2019", "2024-03-11" and "6 months ago" are all things
            // a patient actually says, and a date column would force a false precision.
            $table->string('started')->nullable();
            $table->boolean('active')->default(true);
            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            $table->index(['patient_id', 'active']);
        });

        Schema::create('chronic_conditions', function (Blueprint $table) {
            $table->id();
            $table->string('patient_id');
            $table->string('name');
            $table->string('since')->nullable();
            $table->text('notes')->nullable();
            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            $table->index('patient_id');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('chronic_conditions');
        Schema::dropIfExists('patient_medications');
        Schema::dropIfExists('allergies');
        Schema::dropIfExists('patients');
    }
};
