<?php

namespace Tests\Feature;

use App\Engine\EngineClient;
use App\Models\ReferenceDocument;
use App\Models\User;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Mockery;
use Tests\TestCase;

/**
 * The assistant's reference library.
 *
 * Two properties matter more than the rest.
 *
 * The curated guidelines cannot be removed through this application, checked from both
 * directions: no route can name them, and a caller who forges a row is refused by the
 * engine rather than quietly succeeding.
 *
 * And the library is the administrator's to change. A doctor reads it — knowing what the
 * assistant reasons from is part of reading its suggestions honestly — but an added
 * document is quoted into every clinician's differentials, which makes editing the corpus
 * a configuration change rather than clinical work.
 *
 * The engine is mocked throughout. Embedding a document takes real seconds and loads a
 * model; what is under test is who may do what, and what gets recorded — none of which
 * depends on a vector store actually being there.
 */
class ReferenceLibraryTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        $this->seed(ClinicalRecordSeeder::class);
    }

    private function doctor(): User
    {
        return User::where('role', 'doctor')->firstOrFail();
    }

    private function admin(): User
    {
        return User::where('role', 'admin')->firstOrFail();
    }

    /** The engine's view of the library: two curated documents, plus whatever is passed. */
    private function fakeEngine(array $added = []): Mockery\MockInterface
    {
        $engine = Mockery::mock(EngineClient::class);

        $engine->shouldReceive('references')->andReturn([
            'documents' => array_merge([
                ['source_name' => 'GINA-2026.pdf', 'title' => 'GINA 2026',
                 'citation' => 'GINA, 2026. Asthma', 'origin' => 'builtin', 'chunks' => 185],
                ['source_name' => 'GOLD-2026.pdf', 'title' => 'GOLD 2026',
                 'citation' => 'GOLD, 2026. COPD', 'origin' => 'builtin', 'chunks' => 594],
            ], $added),
        ])->byDefault();

        $this->app->instance(EngineClient::class, $engine);

        return $engine;
    }

    // ---- reading ---------------------------------------------------------------------

    public function test_the_library_lists_what_the_assistant_can_cite(): void
    {
        $this->fakeEngine();
        $this->actingAs($this->doctor(), 'sanctum');

        $this->getJson('/api/references')
            ->assertOk()
            ->assertJsonCount(2, 'documents')
            ->assertJsonPath('documents.0.title', 'GINA 2026')
            ->assertJsonPath('documents.0.chunks', 185);
    }

    public function test_curated_documents_are_never_marked_removable(): void
    {
        $this->fakeEngine();
        $this->actingAs($this->doctor(), 'sanctum');

        $documents = $this->getJson('/api/references')->json('documents');

        foreach ($documents as $document) {
            $this->assertFalse($document['removable'], "{$document['title']} must not be removable");
        }
    }

    public function test_a_nurse_may_read_the_library(): void
    {
        // Knowing what the assistant reasons from is part of reading its suggestions.
        $this->fakeEngine();
        $this->actingAs(User::where('role', 'nurse')->firstOrFail(), 'sanctum');

        $this->getJson('/api/references')->assertOk();
    }

    public function test_a_patient_account_cannot_read_the_library(): void
    {
        $this->fakeEngine();

        // Built here rather than taken from the seed, which has no patient login. The rule
        // is worth pinning down regardless: what the assistant reasons from is a clinical
        // matter, and a patient reading it would be reading over the clinician's shoulder.
        $patient = User::create([
            'name' => 'Kamel Bouzid',
            'email' => 'kamel@patients.test',
            'password' => bcrypt('password'),
            'role' => 'patient',
            'patient_id' => 'P-001',
            'is_active' => true,
        ]);

        $this->actingAs($patient, 'sanctum');
        $this->getJson('/api/references')->assertForbidden();
    }

    // ---- adding ----------------------------------------------------------------------

    public function test_an_administrator_adds_a_document_and_it_is_recorded_against_them(): void
    {
        $admin = $this->admin();
        $engine = $this->fakeEngine();

        $engine->shouldReceive('addReference')->once()->andReturn([
            'source_name' => 'protocol.txt',
            'title' => 'Clinic Asthma Protocol',
            'citation' => 'Clinic, 2026. Clinic Asthma Protocol',
            'chunks' => 4,
            'origin' => 'added',
        ]);

        $this->actingAs($admin, 'sanctum');

        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->createWithContent('protocol.txt', 'Wheeze protocol.'),
            'title' => 'Clinic Asthma Protocol',
            'publisher' => 'Clinic',
            'year' => 2026,
        ])->assertCreated()->assertJsonPath('chunks', 4);

        $this->assertDatabaseHas('reference_documents', [
            'source_name' => 'protocol.txt',
            'title' => 'Clinic Asthma Protocol',
            'chunks' => 4,
            'added_by' => $admin->id,
        ]);
    }

    public function test_adding_a_document_is_audited(): void
    {
        $admin = $this->admin();
        $engine = $this->fakeEngine();
        $engine->shouldReceive('addReference')->andReturn([
            'source_name' => 'protocol.txt', 'chunks' => 2, 'origin' => 'added',
        ]);

        $this->actingAs($admin, 'sanctum');
        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->createWithContent('protocol.txt', 'text'),
            'title' => 'Protocol',
        ])->assertCreated();

        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $admin->id,
            'action' => 'reference.added',
        ]);
    }

    public function test_a_doctor_cannot_add_a_document(): void
    {
        // Reading, yes. Changing what every colleague's differentials are built from, no.
        $this->fakeEngine();
        $this->actingAs($this->doctor(), 'sanctum');

        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->createWithContent('protocol.txt', 'text'),
            'title' => 'Protocol',
        ])->assertForbidden();

        $this->assertDatabaseCount('reference_documents', 0);
    }

    public function test_a_nurse_cannot_add_a_document(): void
    {
        $this->fakeEngine();
        $this->actingAs(User::where('role', 'nurse')->firstOrFail(), 'sanctum');

        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->createWithContent('protocol.txt', 'text'),
            'title' => 'Protocol',
        ])->assertForbidden();

        $this->assertDatabaseCount('reference_documents', 0);
    }

    public function test_a_file_type_the_chunker_cannot_read_is_refused(): void
    {
        $this->fakeEngine();
        $this->actingAs($this->admin(), 'sanctum');

        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->create('slides.pptx', 100),
            'title' => 'Slides',
        ])->assertStatus(422);

        $this->assertDatabaseCount('reference_documents', 0);
    }

    public function test_a_document_without_a_title_is_refused(): void
    {
        $this->fakeEngine();
        $this->actingAs($this->admin(), 'sanctum');

        $this->postJson('/api/references', [
            'file' => UploadedFile::fake()->createWithContent('protocol.txt', 'text'),
        ])->assertStatus(422);
    }

    // ---- removing --------------------------------------------------------------------

    public function test_an_administrator_removes_an_added_document(): void
    {
        $admin = $this->admin();
        $engine = $this->fakeEngine();
        $engine->shouldReceive('removeReference')->once()->with('protocol.txt')
            ->andReturn(['source_name' => 'protocol.txt', 'removed_chunks' => 4]);

        $document = ReferenceDocument::create([
            'source_name' => 'protocol.txt',
            'original_filename' => 'protocol.txt',
            'title' => 'Protocol',
            'chunks' => 4,
            'added_by' => $admin->id,
        ]);

        $this->actingAs($admin, 'sanctum');

        $this->deleteJson("/api/references/{$document->source_name}")
            ->assertOk()
            ->assertJsonPath('removed_chunks', 4);

        $this->assertDatabaseMissing('reference_documents', ['id' => $document->id]);
    }

    public function test_removal_is_audited_with_what_was_removed(): void
    {
        $admin = $this->admin();
        $engine = $this->fakeEngine();
        $engine->shouldReceive('removeReference')->andReturn(['removed_chunks' => 4]);

        $document = ReferenceDocument::create([
            'source_name' => 'protocol.txt', 'original_filename' => 'protocol.txt',
            'title' => 'Protocol', 'chunks' => 4, 'added_by' => $admin->id,
        ]);

        $this->actingAs($admin, 'sanctum');
        $this->deleteJson("/api/references/{$document->source_name}")->assertOk();

        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $admin->id,
            'action' => 'reference.removed',
        ]);
    }

    /**
     * The property this whole feature turns on.
     *
     * Removal binds to `reference_documents`, which only ever holds what was added through
     * the application. A curated guideline has no row, so there is no id that names it —
     * the request cannot be expressed, let alone authorised.
     */
    public function test_a_curated_guideline_cannot_be_named_by_the_removal_route(): void
    {
        $engine = $this->fakeEngine();
        $engine->shouldNotReceive('removeReference');

        $this->actingAs($this->admin(), 'sanctum');

        foreach (['GINA-2026.pdf', 'GOLD-2026.pdf', 'invented.txt'] as $attempt) {
            $this->deleteJson('/api/references/'.$attempt)->assertNotFound();
        }
    }

    public function test_the_engine_refuses_a_curated_document_independently(): void
    {
        // Belt and braces: even if a row somehow pointed at a curated document, the engine
        // checks the chunks themselves and refuses.
        $engine = $this->fakeEngine();
        $engine->shouldReceive('removeReference')->once()
            ->andThrow(new \App\Engine\EngineRefusedException(
                'GINA-2026.pdf is part of the curated reference library.',
                'protected_document',
            ));

        $document = ReferenceDocument::create([
            'source_name' => 'GINA-2026.pdf', 'original_filename' => 'GINA-2026.pdf',
            'title' => 'GINA', 'chunks' => 185, 'added_by' => $this->admin()->id,
        ]);

        $this->actingAs($this->admin(), 'sanctum');

        $this->deleteJson("/api/references/{$document->source_name}")
            ->assertStatus(409)
            ->assertJsonPath('reason', 'protected_document');

        // The row survives, because the corpus did.
        $this->assertDatabaseHas('reference_documents', ['id' => $document->id]);
    }

    public function test_a_doctor_cannot_remove_a_document(): void
    {
        $engine = $this->fakeEngine();
        $engine->shouldNotReceive('removeReference');

        $document = ReferenceDocument::create([
            'source_name' => 'protocol.txt', 'original_filename' => 'protocol.txt',
            'title' => 'Protocol', 'chunks' => 4, 'added_by' => $this->admin()->id,
        ]);

        $this->actingAs($this->doctor(), 'sanctum');

        $this->deleteJson("/api/references/{$document->source_name}")->assertForbidden();
        $this->assertDatabaseHas('reference_documents', ['id' => $document->id]);
    }

    public function test_a_nurse_cannot_remove_a_document(): void
    {
        $engine = $this->fakeEngine();
        $engine->shouldNotReceive('removeReference');

        $document = ReferenceDocument::create([
            'source_name' => 'protocol.txt', 'original_filename' => 'protocol.txt',
            'title' => 'Protocol', 'chunks' => 4, 'added_by' => $this->admin()->id,
        ]);

        $this->actingAs(User::where('role', 'nurse')->firstOrFail(), 'sanctum');

        $this->deleteJson("/api/references/{$document->source_name}")->assertForbidden();
        $this->assertDatabaseHas('reference_documents', ['id' => $document->id]);
    }

    // ---- attribution -----------------------------------------------------------------

    public function test_the_listing_says_who_added_each_document(): void
    {
        $doctor = $this->doctor();

        $this->fakeEngine([[
            'source_name' => 'protocol.txt', 'title' => 'Protocol',
            'citation' => 'Clinic, 2026. Protocol', 'origin' => 'added', 'chunks' => 4,
        ]]);

        ReferenceDocument::create([
            'source_name' => 'protocol.txt', 'original_filename' => 'my protocol.txt',
            'title' => 'Protocol', 'chunks' => 4, 'added_by' => $doctor->id,
        ]);

        $this->actingAs($doctor, 'sanctum');

        $added = collect($this->getJson('/api/references')->json('documents'))
            ->firstWhere('source_name', 'protocol.txt');

        $this->assertSame($doctor->name, $added['added_by']);
        $this->assertSame('my protocol.txt', $added['original_filename']);
        $this->assertTrue($added['removable']);
    }

    public function test_a_document_the_engine_has_but_this_application_does_not_is_not_removable(): void
    {
        // Added straight into the corpus by the CLI, say. It is real evidence and should be
        // listed — but this application has no provenance for it and will not delete it.
        $this->fakeEngine([[
            'source_name' => 'orphan.txt', 'title' => 'Orphan',
            'citation' => 'Orphan', 'origin' => 'added', 'chunks' => 2,
        ]]);

        $this->actingAs($this->doctor(), 'sanctum');

        $orphan = collect($this->getJson('/api/references')->json('documents'))
            ->firstWhere('source_name', 'orphan.txt');

        $this->assertFalse($orphan['removable']);
        $this->assertNull($orphan['added_by']);
    }
}
