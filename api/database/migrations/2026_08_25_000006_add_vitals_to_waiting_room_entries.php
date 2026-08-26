<?php

/**
 * Vitals taken at triage, on the waiting-room entry.
 *
 * The original migration said vitals belong to the visit and should not be duplicated here.
 * That was wrong about the sequence: the nurse measures them before the doctor opens a
 * consultation, and no visit exists yet to hold them. Creating one early is not an option —
 * a visit written before a diagnosis is chosen is exactly the abandoned record the whole
 * design works to avoid.
 *
 * Nor is this duplication once the visit exists. Triage vitals and consultation vitals are
 * different observations taken at different times by different people, and a saturation of
 * 88% in the waiting room that reads 94% twenty minutes later is a clinical fact, not a
 * correction. Both are kept; the doctor's screen offers the triage set as a starting point
 * and records who measured it.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->decimal('temperature_c', 4, 1)->nullable()->after('status');
            $table->unsignedSmallInteger('heart_rate')->nullable()->after('temperature_c');
            $table->unsignedSmallInteger('respiratory_rate')->nullable()->after('heart_rate');
            // A string here for the same reason it is one on the visit: "128/76 (left arm)".
            $table->string('blood_pressure')->nullable()->after('respiratory_rate');
            $table->decimal('spo2', 4, 1)->nullable()->after('blood_pressure');
            $table->decimal('weight_kg', 5, 1)->nullable()->after('spo2');
            $table->decimal('height_cm', 5, 1)->nullable()->after('weight_kg');
            $table->timestamp('vitals_taken_at')->nullable()->after('height_cm');
        });
    }

    public function down(): void
    {
        Schema::table('waiting_room_entries', function (Blueprint $table) {
            $table->dropColumn([
                'temperature_c', 'heart_rate', 'respiratory_rate', 'blood_pressure',
                'spo2', 'weight_kg', 'height_cm', 'vitals_taken_at',
            ]);
        });
    }
};
