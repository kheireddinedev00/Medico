<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * The triage agent's output on the queue row.
 *
 * `suggested_priority` and `nurse_priority` already existed, reserved for this. They stay
 * integer ranks because that is what the queue sorts on, and the engine now serialises
 * exactly that integer as `priority_rank` — so the ordering policy is decided in one place
 * and this side only obeys it. The matching `_label` columns hold the word the engine used,
 * so no PHP anywhere has to know that 3 means CRITICAL.
 *
 * `triage_result` keeps the whole decision as it was returned. That is the audit trail: the
 * NEWS2 breakdown, which red flags fired, what the model contributed, which rule set
 * version scored it. A priority without the reasoning behind it is not reviewable, and this
 * project's whole claim is that its AI decisions are reviewable.
 *
 * The nurse's override is stored beside the agent's answer and never over it, for the same
 * reason `visits` keeps the assistant's differential apart from the working diagnosis.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            /*
             * What the patient says is wrong, recorded at the desk.
             *
             * This is new, and the agent does not work without it. Half of triage is the
             * complaint, not the observations: crushing chest pain with a perfect set of
             * vitals is the case the whole red-flag layer exists for, and until now the
             * queue had nowhere to write it. The visit's chief complaint is not a
             * substitute — a visit only exists once the doctor opens one, which is after
             * the ordering decision has already been made.
             */
            $table->text('chief_complaint')->nullable();
            $table->text('triage_notes')->nullable();

            /*
             * Two facts that change which thresholds apply, so neither may be guessed.
             *
             * `is_pregnant` is nullable on purpose: null means nobody asked, false means
             * asked and no. The engine treats those differently and so must this column —
             * defaulting it to false would silently assert something nobody established.
             *
             * `hypercapnic_target_range` selects NEWS2 Scale 2 and is the single most
             * dangerous field here. It may only be set from a documented prescribed 88-92%
             * target, never inferred from a COPD diagnosis: a COPD patient without a
             * target range who desaturates to 89% scores 0 on Scale 2 and 3 on Scale 1,
             * and the second one is correct.
             */
            $table->boolean('is_pregnant')->nullable();
            $table->boolean('hypercapnic_target_range')->default(false);

            /*
             * Two NEWS2 parameters the vitals migration did not have.
             *
             * Without them the scale can only ever score five of its seven parameters, and
             * every patient comes back as incomplete. Both are nullable and null means not
             * assessed — `on_oxygen = false` is a statement that the patient is breathing
             * air, which is a real observation and must not be the default for a form
             * nobody filled in. A normal saturation held up by oxygen is not a normal
             * saturation, and that is exactly the distinction these two columns carry.
             */
            $table->boolean('on_oxygen')->nullable();
            $table->string('oxygen_delivery')->nullable();
            // ACVPU: alert | confusion | voice | pain | unresponsive. New confusion scores
            // the same as a reduced conscious level, which is why the C is in the scale.
            $table->string('consciousness')->nullable();

            // The word behind the rank already in `suggested_priority`.
            $table->string('suggested_priority_label')->nullable()->after('suggested_priority');

            // OK | INSUFFICIENT_DATA | OUT_OF_SCOPE. Not a priority — a statement about
            // whether the agent could assess this patient at all, which the nurse needs to
            // see even when a priority was produced anyway.
            $table->string('triage_status')->nullable();

            // The engine's id for this decision, so a row here can be matched to the
            // engine's own record of what it was asked.
            $table->string('triage_request_id')->nullable();
            $table->string('triage_ruleset_version')->nullable();

            // True when the model was unreachable and the priority came from the rules
            // alone. The result is still valid; the UI says so rather than implying the
            // full pipeline ran.
            $table->boolean('triage_degraded')->default(false);

            // The complete TriageResult as returned. Reasons, NEWS2 parameters, red flags,
            // data quality problems, model contribution.
            $table->json('triage_result')->nullable();

            // --- the nurse's override ---
            $table->string('nurse_priority_label')->nullable();
            $table->text('nurse_priority_reason')->nullable();
            $table->foreignId('nurse_priority_by')->nullable()->constrained('users')->nullOnDelete();
            $table->timestamp('nurse_priority_at')->nullable();

            // The queue is read far more often than it is written, and always in this
            // order.
            $table->index(['status', 'suggested_priority', 'arrived_at']);
        });
    }

    public function down(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->dropIndex(['status', 'suggested_priority', 'arrived_at']);
            $table->dropConstrainedForeignId('nurse_priority_by');
            $table->dropColumn([
                'chief_complaint',
                'triage_notes',
                'is_pregnant',
                'hypercapnic_target_range',
                'on_oxygen',
                'oxygen_delivery',
                'consciousness',
                'suggested_priority_label',
                'triage_status',
                'triage_request_id',
                'triage_ruleset_version',
                'triage_degraded',
                'triage_result',
                'nurse_priority_label',
                'nurse_priority_reason',
                'nurse_priority_at',
            ]);
        });
    }
};
