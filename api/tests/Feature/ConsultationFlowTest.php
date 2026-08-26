<?php

namespace Tests\Feature;

use App\Models\Patient;
use App\Models\User;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The API, end to end, against a running clinical engine.
 *
 * These are integration tests by intent. The thing most likely to break in this project is
 * not either half on its own — both have their own suites — but the agreement between them:
 * a column renamed here, a field added there, and the chart stops validating. Only a test
 * that crosses the boundary catches that.
 *
 * They skip rather than fail when the engine is not running, so the suite stays usable
 * without a Python process. Skipped is honest; passing would not be.
 *
 * None of these calls the model. Every route exercised here is the state machine or a
 * projection, so the suite costs nothing to run and cannot be rate-limited.
 */
class ConsultationFlowTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        if (! $this->engineIsUp()) {
            $this->markTestSkipped(
                'The clinical engine is not running. Start it from the project root: '
                .'venv/Scripts/python -m uvicorn service.app:app --port 8001'
            );
        }

        $this->seed(ClinicalRecordSeeder::class);
    }

    private function engineIsUp(): bool
    {
        try {
            return Http::timeout(3)->get(config('engine.url').'/health')->successful();
        } catch (\Throwable) {
            return false;
        }
    }

    private function actingAsRole(string $role): User
    {
        $user = User::where('role', $role)->firstOrFail();

        return $this->actingAs($user, 'sanctum') ? $user : $user;
    }

    // --- authentication and roles -------------------------------------------------

    public function test_login_returns_a_token_and_the_users_role(): void
    {
        $response = $this->postJson('/api/login', [
            'email' => 'doctor@clinic.test',
            'password' => 'password',
        ]);

        $response->assertOk()
            ->assertJsonPath('user.role', 'doctor')
            ->assertJsonStructure(['token', 'user' => ['id', 'name', 'email', 'role']]);
    }

    public function test_bad_credentials_do_not_reveal_whether_the_account_exists(): void
    {
        $missing = $this->postJson('/api/login', ['email' => 'nobody@clinic.test', 'password' => 'x']);
        $wrong = $this->postJson('/api/login', ['email' => 'doctor@clinic.test', 'password' => 'x']);

        $missing->assertStatus(422);
        $wrong->assertStatus(422);
        $this->assertSame(
            $missing->json('errors.email'),
            $wrong->json('errors.email'),
            'A different message for a real account tells an attacker which emails exist.'
        );
    }

    public function test_a_nurse_cannot_open_a_consultation(): void
    {
        $this->actingAsRole('nurse');

        $this->postJson('/api/consultations', ['patient_id' => 'P-001'])
            ->assertStatus(403)
            ->assertJsonPath('role', 'nurse');
    }

    public function test_a_patient_cannot_read_another_patients_chart(): void
    {
        $patientUser = User::create([
            'name' => 'Lina Cherif',
            'email' => 'lina@patients.test',
            'password' => bcrypt('password'),
            'role' => User::ROLE_PATIENT,
            'patient_id' => 'P-002',
        ]);

        $this->actingAs($patientUser, 'sanctum');

        $this->getJson('/api/patients/P-002')->assertOk();
        $this->getJson('/api/patients/P-001')->assertStatus(403);
    }

    public function test_unauthenticated_requests_are_rejected(): void
    {
        $this->getJson('/api/patients')->assertStatus(401);
    }

    // --- the workflow --------------------------------------------------------------

    public function test_opening_a_consultation_does_not_create_a_record(): void
    {
        $this->actingAsRole('doctor');
        $before = Patient::find('P-002')->visits()->count();

        $response = $this->postJson('/api/consultations', ['patient_id' => 'P-002']);

        $response->assertCreated()->assertJsonPath('persisted', false);

        $this->assertSame(
            $before,
            Patient::find('P-002')->visits()->count(),
            'A consultation the doctor may yet abandon must not be in the record.'
        );
        $this->assertDatabaseMissing('visits', ['id' => $response->json('visit.id')]);
    }

    public function test_findings_are_held_but_not_recorded_until_a_diagnosis_is_chosen(): void
    {
        $this->actingAsRole('doctor');
        $visitId = $this->postJson('/api/consultations', ['patient_id' => 'P-002'])->json('visit.id');

        $this->postJson("/api/consultations/{$visitId}/findings", [
            'chief_complaint' => 'Wheeze and night cough',
            'symptoms' => ['wheeze', 'nocturnal cough'],
            'vitals' => ['spo2' => 94, 'respiratory_rate' => 22],
        ])->assertOk()->assertJsonPath('persisted', false);

        $this->assertDatabaseMissing('visits', ['id' => $visitId]);

        // The decision is what creates the record — and the findings taken before it must
        // survive into that record rather than being lost with the draft.
        $this->postJson("/api/consultations/{$visitId}/diagnosis", [
            'label' => 'Asthma exacerbation',
        ])->assertOk()->assertJsonPath('persisted', true);

        $this->assertDatabaseHas('visits', [
            'id' => $visitId,
            'status' => 'ICD10_SELECTION',
            'working_diagnosis_label' => 'Asthma exacerbation',
            'chief_complaint' => 'Wheeze and night cough',
        ]);
    }

    public function test_the_full_investigation_path(): void
    {
        $this->actingAsRole('doctor');
        $visitId = $this->postJson('/api/consultations', ['patient_id' => 'P-003'])->json('visit.id');

        $this->postJson("/api/consultations/{$visitId}/findings", [
            'chief_complaint' => 'Productive cough and fever',
            'symptoms' => ['cough', 'fever'],
            'vitals' => ['temperature_c' => 38.4, 'spo2' => 92, 'blood_pressure' => '128/76'],
        ])->assertOk();

        $this->postJson("/api/consultations/{$visitId}/diagnosis", [
            'label' => 'Community-acquired pneumonia',
        ])->assertOk();

        // Codes are checked against the curated list, so an invented one is refused here
        // before it ever reaches the engine.
        $this->postJson("/api/consultations/{$visitId}/diagnosis/code", ['code' => 'Z99.9'])
            ->assertStatus(422);

        $this->postJson("/api/consultations/{$visitId}/diagnosis/code", ['code' => 'J18.9'])
            ->assertOk();

        $this->postJson("/api/consultations/{$visitId}/investigate")->assertOk();

        $this->postJson("/api/consultations/{$visitId}/investigations", [
            'investigations' => [
                ['name' => 'Chest X-ray', 'category' => 'radiology', 'rationale' => 'Confirm consolidation'],
                ['name' => 'CRP', 'category' => 'laboratory'],
            ],
        ])->assertOk()->assertJsonPath('visit.status', 'WAITING_FOR_TESTS');

        $this->postJson("/api/consultations/{$visitId}/results", [
            'summary' => 'Chest X-ray: right lower lobe consolidation. CRP 142 mg/L.',
            'resulted' => ['Chest X-ray', 'CRP'],
        ])->assertOk()->assertJsonPath('visit.status', 'RESULTS_REVIEW');

        $this->postJson("/api/consultations/{$visitId}/treat")->assertOk();

        $this->postJson("/api/consultations/{$visitId}/prescribe", [
            'medications' => [
                ['name' => 'Doxycycline', 'dose' => '100 mg', 'frequency' => 'twice daily', 'duration' => '5 days'],
            ],
        ])->assertOk();

        $this->postJson("/api/consultations/{$visitId}/complete")->assertOk()
            ->assertJsonPath('visit.status', 'COMPLETED');

        $this->assertDatabaseHas('investigations', ['visit_id' => $visitId, 'name' => 'CRP', 'status' => 'resulted']);
        $this->assertDatabaseHas('prescribed_medications', ['visit_id' => $visitId, 'name' => 'Doxycycline']);
        $this->assertDatabaseHas('visits', ['id' => $visitId, 'working_diagnosis_icd10' => 'J18.9']);
    }

    public function test_the_workflow_cannot_be_skipped_through_the_api(): void
    {
        $this->actingAsRole('doctor');
        $visitId = $this->postJson('/api/consultations', ['patient_id' => 'P-002'])->json('visit.id');

        // No diagnosis has been chosen, so there is nothing to close.
        $this->postJson("/api/consultations/{$visitId}/complete")
            ->assertStatus(409)
            ->assertJsonPath('reason', 'invalid_transition');

        // Nor can results be recorded for investigations nobody ordered.
        $this->postJson("/api/consultations/{$visitId}/diagnosis", ['label' => 'Asthma exacerbation'])->assertOk();
        $this->postJson("/api/consultations/{$visitId}/results", ['summary' => 'Normal.'])
            ->assertStatus(409);
    }

    public function test_a_draft_belongs_to_the_clinician_holding_it(): void
    {
        $this->actingAsRole('doctor');
        $visitId = $this->postJson('/api/consultations', ['patient_id' => 'P-002'])->json('visit.id');

        $other = User::create([
            'name' => 'Dr. Second',
            'email' => 'second@clinic.test',
            'password' => bcrypt('password'),
            'role' => User::ROLE_DOCTOR,
        ]);
        $this->actingAs($other, 'sanctum');

        // An unsaved consultation is not shared. It is not in the database, and another
        // clinician's draft cache is not readable from here.
        $this->postJson("/api/consultations/{$visitId}/findings", ['chief_complaint' => 'x'])
            ->assertStatus(404);
    }

    // --- projections ----------------------------------------------------------------

    public function test_next_states_comes_from_the_engines_own_table(): void
    {
        $this->actingAsRole('doctor');

        $this->getJson('/api/consultations/V-002/next-states')
            ->assertOk()
            ->assertJsonPath('status', 'WAITING_FOR_TESTS')
            ->assertJsonPath('next', ['RESULTS_REVIEW']);
    }

    public function test_soap_note_is_assembled_from_the_record(): void
    {
        $this->actingAsRole('doctor');

        $response = $this->getJson('/api/consultations/V-001/soap')->assertOk();

        $this->assertNotEmpty($response->json('subjective.chief_complaint'));
        // The allergy must reach the note — it is the thing a prescriber has to see.
        $this->assertNotEmpty($response->json('subjective.allergies'));
    }

    public function test_the_workflow_table_is_served_to_the_client(): void
    {
        $this->actingAsRole('doctor');

        $this->getJson('/api/workflow')
            ->assertOk()
            ->assertJsonPath('transitions.INITIAL_ASSESSMENT', ['ICD10_SELECTION'])
            ->assertJsonPath('transitions.COMPLETED', []);
    }
}
