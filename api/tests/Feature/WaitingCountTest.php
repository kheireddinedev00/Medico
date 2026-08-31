<?php

namespace Tests\Feature;

use App\Models\User;
use App\Models\WaitingRoomEntry;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * The count behind the badge on the navigation.
 *
 * Its own endpoint because the sidebar asks on a timer and the full dashboard was far too
 * much to build for one integer — for an administrator it grouped the audit log and built a
 * fortnight's trend, then threw all of it away.
 *
 * The scoping is what these tests are really for. A doctor is told how many are waiting for
 * *them*; get that wrong and the badge quietly reports the whole clinic's queue to someone
 * who can only see their own.
 */
class WaitingCountTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        $this->seed(ClinicalRecordSeeder::class);
    }

    /** @return array{0: User, 1: User} two distinct doctors */
    private function twoDoctors(): array
    {
        $doctors = User::where('role', 'doctor')->orderBy('id')->take(2)->get();

        return [$doctors[0], $doctors[1]];
    }

    private function queue(string $patientId, ?int $doctorId, string $status = 'waiting'): void
    {
        WaitingRoomEntry::create([
            'patient_id' => $patientId,
            'doctor_id' => $doctorId,
            'nurse_id' => User::where('role', 'nurse')->firstOrFail()->id,
            'arrived_at' => now(),
            'status' => $status,
        ]);
    }

    public function test_a_doctor_is_counted_only_their_own_waiting_patients(): void
    {
        [$mine, $theirs] = $this->twoDoctors();

        $this->queue('P-001', $mine->id);
        $this->queue('P-002', $theirs->id);
        // Nobody has been given this one yet: in no doctor's count.
        $this->queue('P-003', null);

        $this->actingAs($mine, 'sanctum');

        $this->getJson('/api/stats/waiting')
            ->assertOk()
            ->assertJsonPath('waiting', 1);
    }

    public function test_a_nurse_is_counted_the_whole_room(): void
    {
        [$mine, $theirs] = $this->twoDoctors();

        $this->queue('P-001', $mine->id);
        $this->queue('P-002', $theirs->id);
        $this->queue('P-003', null);

        $this->actingAs(User::where('role', 'nurse')->firstOrFail(), 'sanctum');

        $this->getJson('/api/stats/waiting')
            ->assertOk()
            ->assertJsonPath('waiting', 3);
    }

    public function test_someone_with_the_doctor_is_not_counted_as_waiting(): void
    {
        [$doctor] = $this->twoDoctors();

        $this->queue('P-001', $doctor->id, 'waiting');
        $this->queue('P-002', $doctor->id, 'in_consultation');

        $this->actingAs($doctor, 'sanctum');

        // Being seen is not waiting, but they are still in the room — so they are reported
        // separately rather than dropped.
        $this->getJson('/api/stats/waiting')
            ->assertOk()
            ->assertJsonPath('waiting', 1)
            ->assertJsonPath('in_consultation', 1);
    }

    public function test_someone_who_has_left_is_counted_nowhere(): void
    {
        [$doctor] = $this->twoDoctors();

        $this->queue('P-001', $doctor->id, 'completed');
        $this->queue('P-002', $doctor->id, 'left');

        $this->actingAs($doctor, 'sanctum');

        $this->getJson('/api/stats/waiting')
            ->assertOk()
            ->assertJsonPath('waiting', 0)
            ->assertJsonPath('in_consultation', 0);
    }

    public function test_it_answers_the_same_number_the_full_dashboard_does(): void
    {
        /*
         * The reason to check: two endpoints counting the same thing is exactly the shape
         * that drifts. If the badge and the dashboard ever disagree, one of them is lying
         * and nobody can tell which.
         */
        [$doctor] = $this->twoDoctors();

        $this->queue('P-001', $doctor->id);
        $this->queue('P-002', $doctor->id);

        $this->actingAs($doctor, 'sanctum');

        $badge = $this->getJson('/api/stats/waiting')->json('waiting');
        $dashboard = $this->getJson('/api/stats')->json('stats.waiting_for_me');

        $this->assertSame($dashboard, $badge);
    }

    public function test_a_patient_account_cannot_read_it(): void
    {
        $patient = User::create([
            'name' => 'Kamel Bouzid',
            'email' => 'kamel@patients.test',
            'password' => bcrypt('password'),
            'role' => 'patient',
            'patient_id' => 'P-001',
            'is_active' => true,
        ]);

        $this->actingAs($patient, 'sanctum');
        $this->getJson('/api/stats/waiting')->assertForbidden();
    }
}
