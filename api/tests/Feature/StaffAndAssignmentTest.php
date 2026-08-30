<?php

namespace Tests\Feature;

use App\Models\User;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * Accounts, and who a waiting patient belongs to.
 *
 * A clinic with several doctors makes two things load-bearing that were free when there was
 * one. Accounts have to come from somewhere trustworthy, and a doctor's queue has to be
 * their queue — a shared list of everybody waiting is not a queue, it is a noticeboard.
 *
 * No engine involved: none of this is clinical reasoning.
 */
class StaffAndAssignmentTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        $this->seed(ClinicalRecordSeeder::class);
    }

    private function admin(): User
    {
        return User::where('role', 'admin')->firstOrFail();
    }

    private function nurse(): User
    {
        return User::where('role', 'nurse')->firstOrFail();
    }

    /** @return array{0: User, 1: User} two distinct doctors */
    private function twoDoctors(): array
    {
        $doctors = User::where('role', 'doctor')->orderBy('id')->take(2)->get();
        $this->assertCount(2, $doctors, 'the seed needs two doctors for these tests');

        return [$doctors[0], $doctors[1]];
    }

    // ---- accounts ----------------------------------------------------------------

    public function test_an_administrator_creates_a_doctor_account(): void
    {
        $this->actingAs($this->admin(), 'sanctum');

        $this->postJson('/api/staff', [
            'name' => 'Dr. Sofiane Meziane',
            'email' => 'sofiane@clinic.test',
            'role' => 'doctor',
            'password' => 'correct-horse-8',
        ])->assertCreated()->assertJsonPath('user.role', 'doctor');

        $this->assertDatabaseHas('users', [
            'email' => 'sofiane@clinic.test',
            'role' => 'doctor',
            'is_active' => true,
        ]);
    }

    public function test_a_doctor_cannot_create_accounts(): void
    {
        [$doctor] = $this->twoDoctors();
        $this->actingAs($doctor, 'sanctum');

        $this->postJson('/api/staff', [
            'name' => 'Self Promoted',
            'email' => 'self@clinic.test',
            'role' => 'admin',
            'password' => 'correct-horse-8',
        ])->assertForbidden();

        $this->assertDatabaseMissing('users', ['email' => 'self@clinic.test']);
    }

    public function test_a_nurse_cannot_list_accounts(): void
    {
        $this->actingAs($this->nurse(), 'sanctum');

        $this->getJson('/api/staff')->assertForbidden();
    }

    public function test_deactivating_keeps_the_account_and_ends_its_sessions(): void
    {
        [$doctor] = $this->twoDoctors();
        $doctor->createToken('shift');
        $this->assertSame(1, $doctor->tokens()->count());

        $this->actingAs($this->admin(), 'sanctum');
        $this->deleteJson("/api/staff/{$doctor->id}")->assertOk();

        // Still there — their name is on visits and audit rows.
        $this->assertDatabaseHas('users', ['id' => $doctor->id, 'is_active' => false]);
        $this->assertSame(0, $doctor->fresh()->tokens()->count());
    }

    public function test_an_administrator_cannot_lock_themselves_out(): void
    {
        $admin = $this->admin();
        $this->actingAs($admin, 'sanctum');

        $this->deleteJson("/api/staff/{$admin->id}")->assertStatus(422);
        $this->patchJson("/api/staff/{$admin->id}", ['role' => 'nurse'])->assertStatus(422);

        $this->assertDatabaseHas('users', ['id' => $admin->id, 'role' => 'admin', 'is_active' => true]);
    }

    public function test_a_password_reset_revokes_every_token(): void
    {
        [, $other] = $this->twoDoctors();
        $other->createToken('phone');
        $other->createToken('desk');

        $this->actingAs($this->admin(), 'sanctum');
        $this->postJson("/api/staff/{$other->id}/password", ['password' => 'a-new-one-99'])
            ->assertOk();

        $this->assertSame(0, $other->fresh()->tokens()->count());
    }

    // ---- assignment --------------------------------------------------------------

    public function test_a_nurse_assigns_a_doctor_and_only_that_doctor_sees_the_patient(): void
    {
        [$mine, $theirs] = $this->twoDoctors();

        $this->actingAs($this->nurse(), 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');

        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $mine->id])
            ->assertOk()
            ->assertJsonPath('entry.doctor.id', $mine->id);

        $this->actingAs($mine, 'sanctum');
        $this->getJson('/api/waiting-room')->assertOk()->assertJsonPath('entries.0.id', $entryId);

        $this->actingAs($theirs, 'sanctum');
        $this->getJson('/api/waiting-room')->assertOk()->assertJsonCount(0, 'entries');
    }

    public function test_reassignment_moves_the_patient_between_queues(): void
    {
        [$first, $second] = $this->twoDoctors();

        $this->actingAs($this->nurse(), 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $first->id])->assertOk();
        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $second->id])->assertOk();

        $this->actingAs($first, 'sanctum');
        $this->getJson('/api/waiting-room')->assertJsonCount(0, 'entries');

        $this->actingAs($second, 'sanctum');
        $this->getJson('/api/waiting-room')->assertJsonCount(1, 'entries');
    }

    public function test_every_nurse_sees_the_whole_queue_however_it_is_assigned(): void
    {
        [$doctor] = $this->twoDoctors();
        $nurses = User::where('role', 'nurse')->orderBy('id')->take(2)->get();
        $this->assertCount(2, $nurses, 'the seed needs two nurses for this test');

        $this->actingAs($nurses[0], 'sanctum');
        $assigned = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$assigned}/doctor", ['doctor_id' => $doctor->id]);

        // A second nurse adds someone and leaves them unassigned.
        $this->actingAs($nurses[1], 'sanctum');
        $this->postJson('/api/waiting-room', ['patient_id' => 'P-002'])->assertCreated();

        $this->getJson('/api/waiting-room')->assertOk()->assertJsonCount(2, 'entries');
    }

    public function test_an_unassigned_patient_is_in_no_doctors_queue(): void
    {
        [$one, $two] = $this->twoDoctors();

        $this->actingAs($this->nurse(), 'sanctum');
        $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->assertCreated();

        foreach ([$one, $two] as $doctor) {
            $this->actingAs($doctor, 'sanctum');
            $this->getJson('/api/waiting-room')->assertJsonCount(0, 'entries');
        }
    }

    public function test_a_patient_with_the_doctor_cannot_be_reassigned(): void
    {
        [$first, $second] = $this->twoDoctors();

        $this->actingAs($this->nurse(), 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $first->id])->assertOk();

        WaitingRoomEntry::whereKey($entryId)->update(['status' => 'in_consultation']);

        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $second->id])
            ->assertStatus(422);

        $this->assertDatabaseHas('waiting_room_entries', [
            'id' => $entryId,
            'doctor_id' => $first->id,
        ]);
    }

    public function test_a_nurse_cannot_assign_someone_who_is_not_a_doctor(): void
    {
        $this->actingAs($this->nurse(), 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');

        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $this->admin()->id])
            ->assertStatus(422);

        $this->assertDatabaseHas('waiting_room_entries', ['id' => $entryId, 'doctor_id' => null]);
    }

    public function test_a_doctor_can_be_chosen_when_the_patient_arrives(): void
    {
        [$mine, $theirs] = $this->twoDoctors();

        $this->actingAs($this->nurse(), 'sanctum');
        $this->postJson('/api/waiting-room', [
            'patient_id' => 'P-001',
            'doctor_id' => $mine->id,
        ])->assertCreated()->assertJsonPath('entry.doctor.id', $mine->id);

        $this->actingAs($mine, 'sanctum');
        $this->getJson('/api/waiting-room')->assertJsonCount(1, 'entries');

        $this->actingAs($theirs, 'sanctum');
        $this->getJson('/api/waiting-room')->assertJsonCount(0, 'entries');
    }

    public function test_arrival_refuses_a_doctor_id_that_is_not_a_doctor(): void
    {
        $this->actingAs($this->nurse(), 'sanctum');

        $this->postJson('/api/waiting-room', [
            'patient_id' => 'P-001',
            'doctor_id' => $this->admin()->id,
        ])->assertStatus(422);

        $this->assertDatabaseMissing('waiting_room_entries', ['patient_id' => 'P-001']);
    }

    public function test_a_deactivated_doctor_cannot_be_assigned(): void
    {
        [$doctor] = $this->twoDoctors();
        $doctor->update(['is_active' => false]);

        $this->actingAs($this->nurse(), 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');

        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $doctor->id])
            ->assertStatus(422);

        // And they are gone from the nurse's list of who can take a patient.
        $this->getJson('/api/doctors')
            ->assertOk()
            ->assertJsonMissing(['id' => $doctor->id]);
    }

    public function test_the_assignment_is_audited(): void
    {
        [$first, $second] = $this->twoDoctors();
        $nurse = $this->nurse();

        $this->actingAs($nurse, 'sanctum');
        $entryId = $this->postJson('/api/waiting-room', ['patient_id' => 'P-001'])->json('entry.id');
        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $first->id]);
        $this->postJson("/api/waiting-room/{$entryId}/doctor", ['doctor_id' => $second->id]);

        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $nurse->id,
            'action' => 'waiting_room.assigned',
        ]);
    }

    // ---- attribution -------------------------------------------------------------

    public function test_the_chart_says_which_doctor_saw_them(): void
    {
        [$doctor] = $this->twoDoctors();

        // Written directly: the doctor is stamped when a consultation becomes a record, and
        // reaching that point needs the engine. What is under test here is the projection —
        // that "who saw me" survives into the chart.
        Visit::create([
            'id' => 'V-ATTRIB-1',
            'patient_id' => 'P-001',
            'status' => 'COMPLETED',
            'chief_complaint' => 'Cough for five days',
            'doctor_id' => $doctor->id,
        ]);

        $this->actingAs($doctor, 'sanctum');

        $chart = $this->getJson('/api/patients/P-001')->assertOk()->json('visits');
        $visit = collect($chart)->firstWhere('id', 'V-ATTRIB-1');

        $this->assertNotNull($visit, 'the visit should appear in the chart');
        $this->assertSame($doctor->name, $visit['doctor']);
    }

    public function test_a_visit_nobody_claimed_says_so_rather_than_naming_someone(): void
    {
        [$doctor] = $this->twoDoctors();

        Visit::create([
            'id' => 'V-ATTRIB-2',
            'patient_id' => 'P-001',
            'status' => 'COMPLETED',
            'chief_complaint' => 'Imported from the old paper file',
        ]);

        $this->actingAs($doctor, 'sanctum');

        $chart = $this->getJson('/api/patients/P-001')->assertOk()->json('visits');
        $visit = collect($chart)->firstWhere('id', 'V-ATTRIB-2');

        $this->assertNull($visit['doctor']);
    }
}
