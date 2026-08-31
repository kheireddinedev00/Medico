<?php

/**
 * Documents a physician added to the assistant's reference library.
 *
 * The corpus itself lives in the engine — the vector store and its registry. This table is
 * not a second copy of it; it answers a question the engine has no business knowing:
 * **who** put a document into the evidence base, and when.
 *
 * That matters because an added document becomes citable evidence. The assistant retrieves
 * from it and quotes it back into a differential, so a clinical suggestion can now trace to
 * something a named colleague uploaded on a particular Tuesday. Losing that link would
 * leave the corpus with anonymous contents.
 *
 * `source_name` is the join to the engine, unique because the engine keys on it too. It is
 * deliberately not a foreign key to anything: the engine is a separate process with its own
 * storage, and pretending otherwise in the schema would be a fiction.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('reference_documents', function (Blueprint $table) {
            $table->id();

            // What the engine calls this document. The sanitised file name, not the one
            // that was uploaded — see rag/added_documents.py::safe_source_name.
            $table->string('source_name')->unique();
            $table->string('original_filename');

            $table->string('title');
            $table->string('publisher')->nullable();
            $table->unsignedSmallInteger('year')->nullable();
            $table->string('reference')->nullable();

            // How many passages it contributed. Shown so a physician can see that a
            // two-page protocol became four chunks and a long document became four hundred.
            $table->unsignedInteger('chunks')->default(0);

            // Never cascades. If the account is deleted the document stays in the corpus,
            // and "added by someone no longer here" is the truth — better than a row that
            // silently disappears from the provenance trail.
            $table->foreignId('added_by')->nullable()->constrained('users')->nullOnDelete();

            $table->timestamps();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('reference_documents');
    }
};
