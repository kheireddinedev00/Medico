<?php

/**
 * Reference data, the assistant's audit trail, and the waiting room.
 *
 * `icd10_codes` is a read-only lookup seeded from the engine's curated list over
 * /reference/icd10 — one source, copied rather than retyped. There is deliberately no
 * `patient_icd10` join table: a code belongs to an encounter, not to a person, and it
 * lives on the visit as the physician's chosen value.
 *
 * `assistant_runs` and `medication_warnings` are append-only. Together they answer the
 * question the examiners will ask: what exactly was the model told, and what exactly did
 * it say? Nothing in the application should ever update or delete a row in either.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('icd10_codes', function (Blueprint $table) {
            $table->string('code')->primary();
            $table->string('description');
            // Words a doctor would type that are not in the official description
            // ("COVID" for U07.1, "flu" for J11). Matching only; not shown as the name.
            $table->json('synonyms')->nullable();
            $table->timestamps();

            $table->index('description');
        });

        Schema::create('assistant_runs', function (Blueprint $table) {
            $table->id();
            $table->string('visit_id');
            // differential | icd10 | investigations | medications
            $table->string('kind');
            $table->string('model');
            $table->json('raw_response');
            // The retrieved excerpts, with their citations and page numbers. Stored with
            // the run because a citation only means something next to the answer it
            // supported — and because the model may only point at evidence by number,
            // so without this the numbers are unresolvable later.
            $table->json('evidence')->nullable();
            $table->foreignId('requested_by')->nullable()->constrained('users')->nullOnDelete();
            $table->timestamps();

            $table->foreign('visit_id')->references('id')->on('visits')->cascadeOnDelete();
            $table->index(['visit_id', 'kind']);
        });

        Schema::create('medication_warnings', function (Blueprint $table) {
            $table->id();
            $table->foreignId('assistant_run_id')->constrained()->cascadeOnDelete();
            $table->string('drug');
            // withheld — the patient is recorded as allergic to that class, so the drug was
            //            removed from the suggestions entirely.
            // caution  — an interaction, cross-reactivity, or a condition making it harder
            //            to use. Still offered, because that is a prescribing decision.
            $table->string('severity');
            $table->text('reason');
            $table->timestamps();

            $table->index(['assistant_run_id', 'severity']);
        });

        // The nurse's queue. Vitals go onto the visit, not here — this table is about who
        // is waiting and in what order, not about what was measured.
        Schema::create('waiting_room_entries', function (Blueprint $table) {
            $table->id();
            $table->string('patient_id');
            // Null until the doctor opens the consultation and a visit exists.
            $table->string('visit_id')->nullable();
            $table->foreignId('nurse_id')->nullable()->constrained('users')->nullOnDelete();
            $table->timestamp('arrived_at')->useCurrent();
            $table->timestamp('seen_at')->nullable();
            $table->string('status')->default('waiting');   // waiting | in_consultation | left

            // --- room for the triage agent, which is not built yet ---
            // Kept apart for the same reason the differential is kept apart from the
            // working diagnosis: the agent will propose an order, the nurse will choose
            // one, and both have to survive so they can be compared.
            $table->unsignedTinyInteger('suggested_priority')->nullable();
            $table->text('priority_reason')->nullable();
            $table->timestamp('priority_computed_at')->nullable();
            $table->unsignedTinyInteger('nurse_priority')->nullable();

            $table->timestamps();

            $table->foreign('patient_id')->references('id')->on('patients')->cascadeOnDelete();
            $table->foreign('visit_id')->references('id')->on('visits')->nullOnDelete();
            $table->index(['status', 'arrived_at']);
        });

        Schema::create('audit_logs', function (Blueprint $table) {
            $table->id();
            $table->foreignId('user_id')->nullable()->constrained()->nullOnDelete();
            $table->string('action');
            $table->string('subject_type')->nullable();
            $table->string('subject_id')->nullable();
            $table->json('before')->nullable();
            $table->json('after')->nullable();
            $table->string('ip_address', 45)->nullable();
            $table->timestamps();

            $table->index(['subject_type', 'subject_id']);
            $table->index(['user_id', 'created_at']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('audit_logs');
        Schema::dropIfExists('waiting_room_entries');
        Schema::dropIfExists('medication_warnings');
        Schema::dropIfExists('assistant_runs');
        Schema::dropIfExists('icd10_codes');
    }
};
