<?php

namespace Tests\Feature;

use App\Models\User;
use App\Models\WaitingRoomEntry;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * The nurse's queue.
 *
 * No engine is needed for any of this — the waiting room does no clinical reasoning, which
 * is exactly why these tests do not skip when the Python service is down.
 */
class WaitingRoomTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        $this->seed(ClinicalRecordSeeder::class);
    }

    private function asNurse(): User
    {
        $nurse = User::where('role', 'nurse')->firstOrFail();
        $this->actingAs($nurse, 'sanctum');

        return $nurse;
    }

    public function test_a_nurse_registers_an_arrival(): void
    {
        $this->asNurse();

        $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])
            ->assertCreated()
            ->assertJsonPath('entry.status', 'waiting');

        $this->assertDatabaseHas('waiting_room_entries', [
            'patient_id' => 'P-001',
            'status' => 'waiting',
        ]);
    }

    public function test_the_same_patient_is_not_queued_twice(): void
    {
        $this->asNurse();

        $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->assertCreated();
        $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])
            ->assertOk()
            ->assertJsonPath('message', 'That patient is already in the waiting room.');

        $this->assertSame(1, WaitingRoomEntry::where('patient_id', 'P-001')->count());
    }

    public function test_vitals_are_recorded_against_the_queue_entry(): void
    {
        $this->asNurse();
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');

        $this->postJson("/api/waiting-room/{$entryId}/vitals", [
            'temperature_c' => 38.2,
            'heart_rate' => 104,
            'respiratory_rate' => 24,
            'blood_pressure' => '148/92',
            'spo2' => 91,
        ])->assertOk()->assertJsonPath('vitals.spo2', 91);

        $entry = WaitingRoomEntry::find($entryId);
        $this->assertNotNull($entry->vitals_taken_at);
        $this->assertSame('148/92', $entry->blood_pressure);
    }

    public function test_an_unmeasured_vital_stays_null_rather_than_becoming_a_number(): void
    {
        $this->asNurse();
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-002'])->json('entry.id');

        // Only a temperature was taken. Everything else must read as "not measured", which
        // is a different clinical statement from a normal result.
        $this->postJson("/api/waiting-room/{$entryId}/vitals", ['temperature_c' => 37.1])->assertOk();

        $entry = WaitingRoomEntry::find($entryId);
        $this->assertNull($entry->spo2);
        $this->assertNull($entry->blood_pressure);
        $this->assertNull($entry->heart_rate);
    }

    public function test_impossible_vitals_are_rejected(): void
    {
        $this->asNurse();
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');

        $this->postJson("/api/waiting-room/{$entryId}/vitals", ['spo2' => 250])->assertStatus(422);
        $this->postJson("/api/waiting-room/{$entryId}/vitals", ['temperature_c' => 200])->assertStatus(422);
    }

    public function test_the_queue_is_ordered_by_arrival_and_reports_the_wait(): void
    {
        $this->asNurse();

        $first = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'waiting', 'arrived_at' => now()->subMinutes(40),
        ]);
        WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'waiting', 'arrived_at' => now()->subMinutes(5),
        ]);

        $response = $this->getJson('/api/waiting-room')->assertOk();

        $this->assertSame($first->id, $response->json('entries.0.id'));
        $this->assertGreaterThanOrEqual(39, $response->json('entries.0.waiting_minutes'));
        // Nothing has scored these, and the response says so rather than implying low risk.
        $this->assertNull($response->json('entries.0.suggested_priority'));
    }

    public function test_triage_vitals_are_offered_to_the_doctor(): void
    {
        $this->asNurse();
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-003'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$entryId}/vitals", ['spo2' => 93, 'heart_rate' => 88])->assertOk();

        $this->actingAs(User::where('role', 'doctor')->firstOrFail(), 'sanctum');

        $this->getJson('/api/patients/P-003/triage-vitals')
            ->assertOk()
            ->assertJsonPath('vitals.spo2', 93)
            ->assertJsonPath('vitals.heart_rate', 88);
    }

    public function test_a_patient_account_cannot_see_the_queue(): void
    {
        $patientUser = User::create([
            'name' => 'Kamel Bouzid',
            'email' => 'kamel@patients.test',
            'password' => bcrypt('password'),
            'role' => User::ROLE_PATIENT,
            'patient_id' => 'P-001',
        ]);
        $this->actingAs($patientUser, 'sanctum');

        $this->getJson('/api/waiting-room')->assertStatus(403);
    }

    public function test_queue_actions_are_audited(): void
    {
        $nurse = $this->asNurse();
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$entryId}/vitals", ['spo2' => 95])->assertOk();

        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $nurse->id,
            'action' => 'waiting_room.arrived',
        ]);
        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $nurse->id,
            'action' => 'waiting_room.vitals',
        ]);
    }
}
