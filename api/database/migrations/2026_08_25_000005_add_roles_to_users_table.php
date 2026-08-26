<?php

/**
 * Roles, and the link that lets a patient log in and see their own chart.
 *
 * `patient_id` is what separates "a user" from "the person the record is about". A doctor
 * or nurse has no patient_id; a patient user has exactly one, and every query they make is
 * scoped to it. Keeping the link here rather than in a pivot table is deliberate — a pivot
 * would permit a user linked to two patients, which is not a state this system should be
 * able to represent.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('users', function (Blueprint $table) {
            // doctor | nurse | patient | admin
            $table->string('role')->default('doctor')->after('email');
            $table->string('patient_id')->nullable()->after('role');
            $table->boolean('is_active')->default(true)->after('patient_id');

            $table->foreign('patient_id')->references('id')->on('patients')->nullOnDelete();
            $table->index('role');
        });
    }

    public function down(): void
    {
        Schema::table('users', function (Blueprint $table) {
            $table->dropForeign(['patient_id']);
            $table->dropIndex(['role']);
            $table->dropColumn(['role', 'patient_id', 'is_active']);
        });
    }
};
