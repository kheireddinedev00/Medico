<?php

/**
 * A photo and a job title on the account.
 *
 * Neither is clinical data. The photo exists so a doctor glancing at a queue row or an
 * audit entry recognises a colleague faster than they read a name, and the title so
 * "Dr. Amina Belkacem" can be followed by "Respiratory Consultant" where that is useful.
 *
 * The image is stored as a data URL rather than a file on disk. It is capped and downscaled
 * to 256px in the browser before it is ever sent, which puts it in the tens of kilobytes —
 * small enough that a column is simpler and more robust than a storage disk, a public
 * symlink and a serving route, three things that can each be misconfigured on a fresh clone.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('users', function (Blueprint $table) {
            // MEDIUMTEXT via `mediumText`: a 256px avatar is far smaller, but base64 grows
            // by a third and a TEXT column's 64KB ceiling is close enough to matter.
            $table->mediumText('avatar')->nullable()->after('role');
            $table->string('title')->nullable()->after('avatar');
        });
    }

    public function down(): void
    {
        Schema::table('users', function (Blueprint $table) {
            $table->dropColumn(['avatar', 'title']);
        });
    }
};
