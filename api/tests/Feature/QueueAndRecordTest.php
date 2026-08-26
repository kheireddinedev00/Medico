<?php

namespace Tests\Feature;

use App\Models\Investigation;
use App\Models\Report;
use App\Models\User;
use App\Models\Visit;
use App\Models\WaitingRoomEntry;
use Database\Seeders\ClinicalRecordSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * The queue's newer behaviour, and deleting a visit.
 *
 * No engine needed: none of this reasons clinically.
 */
class QueueAndRecordTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        $this->seed(ClinicalRecordSeeder::class);
    }

    private function asRole(string $role): User
    {
        $user = User::where('role', $role)->firstOrFail();
        $this->actingAs($user, 'sanctum');

        return $user;
    }

    // --- how the wait is reported ---------------------------------------------------

    public function test_a_short_wait_is_whole_minutes(): void
    {
        $this->asRole('nurse');
        WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'waiting', 'arrived_at' => now()->subMinutes(25),
        ]);

        $this->getJson('/api/waiting-room')
            ->assertOk()
            ->assertJsonPath('entries.0.waiting_label', '25 min');
    }

    public function test_a_long_wait_is_hours_to_one_decimal(): void
    {
        $this->asRole('nurse');
        WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'waiting', 'arrived_at' => now()->subMinutes(94),
        ]);

        // 94 minutes is 1.5666… hours. One decimal is a number a nurse can act on; the rest
        // is noise dressed as precision.
        $this->getJson('/api/waiting-room')
            ->assertOk()
            ->assertJsonPath('entries.0.waiting_label', '1.6 h');
    }

    // --- who owns the queue -----------------------------------------------------------

    public function test_a_doctor_does_not_manage_the_queue(): void
    {
        $this->asRole('doctor');

        // Reading it is the doctor's business; running it is the nurse's. Two people
        // managing one list is how the queue stops matching the room.
        $this->getJson('/api/waiting-room')->assertOk();
        $this->postJson('/api/waiting-room', ['patient_id' => 'P-002'])->assertStatus(403);
    }

    public function test_recording_vitals_is_the_nurses_job(): void
    {
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'waiting', 'arrived_at' => now(),
        ]);

        $this->asRole('doctor');
        $this->postJson("/api/waiting-room/{$entry->id}/vitals", ['spo2' => 95])->assertStatus(403);

        $this->asRole('nurse');
        $this->postJson("/api/waiting-room/{$entry->id}/vitals", ['spo2' => 95])->assertOk();
    }

    // --- what the arrival is about ------------------------------------------------------

    public function test_an_arrival_can_name_the_visit_it_is_about(): void
    {
        $this->asRole('nurse');

        $response = $this->postJson('/api/waiting-room', [
            'patient_id' => 'P-001',
            'visit_id' => 'V-002',
        ])->assertCreated();

        $response->assertJsonPath('entry.visit_id', 'V-002')
            ->assertJsonPath('entry.visit.status', 'WAITING_FOR_TESTS');
    }

    public function test_resumable_visits_exclude_completed_ones(): void
    {
        $this->asRole('nurse');

        $ids = collect($this->getJson('/api/patients/P-001/resumable-visits')->json('visits'))
            ->pluck('id');

        $this->assertTrue($ids->contains('V-002'), 'An open visit should be resumable.');
        // V-001 is COMPLETED in the fixture. A finished encounter is not reopened; coming
        // back about it is a new visit.
        $this->assertFalse($ids->contains('V-001'), 'A completed visit must not be offered.');
    }

    public function test_a_queue_entry_cannot_point_at_another_patients_visit(): void
    {
        $this->asRole('nurse');
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'waiting', 'arrived_at' => now(),
        ]);

        $this->postJson("/api/waiting-room/{$entry->id}/visit", ['visit_id' => 'V-002'])
            ->assertStatus(422);
    }

    public function test_a_completed_visit_cannot_be_queued_for_resuming(): void
    {
        $this->asRole('nurse');
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'waiting', 'arrived_at' => now(),
        ]);

        $this->postJson("/api/waiting-room/{$entry->id}/visit", ['visit_id' => 'V-001'])
            ->assertStatus(422);
    }

    // --- deleting a visit ----------------------------------------------------------------

    public function test_a_nurse_cannot_delete_a_visit(): void
    {
        $this->asRole('nurse');
        $this->deleteJson('/api/consultations/V-002')->assertStatus(403);
        $this->assertDatabaseHas('visits', ['id' => 'V-002']);
    }

    public function test_a_doctor_can_delete_a_visit_and_it_is_audited(): void
    {
        $doctor = $this->asRole('doctor');

        $this->deleteJson('/api/consultations/V-002')->assertOk();

        $this->assertDatabaseMissing('visits', ['id' => 'V-002']);
        $this->assertDatabaseHas('audit_logs', [
            'user_id' => $doctor->id,
            'action' => 'consultation.deleted',
            'subject_id' => 'V-002',
        ]);
    }

    public function test_deleting_a_visit_keeps_the_reports_and_detaches_them(): void
    {
        $this->asRole('doctor');

        $report = Report::create([
            'id' => 'r-keepme', 'patient_id' => 'P-001', 'visit_id' => 'V-002',
            'kind' => 'laboratory', 'extracted' => ['results' => []], 'uploaded_at' => now(),
        ]);

        $this->deleteJson('/api/consultations/V-002')->assertOk();

        // A result that arrived is the patient's, whatever happens to the encounter that
        // ordered it. Losing it with the visit would be losing clinical data.
        $this->assertDatabaseHas('reports', ['id' => 'r-keepme', 'visit_id' => null]);
        $this->assertNotNull($report->fresh());
    }

    public function test_deleting_a_visit_removes_what_was_decided_in_it(): void
    {
        $this->asRole('doctor');
        $this->assertDatabaseHas('investigations', ['visit_id' => 'V-002']);

        $this->deleteJson('/api/consultations/V-002')->assertOk();

        $this->assertDatabaseMissing('investigations', ['visit_id' => 'V-002']);
    }

    // --- filing a result against the test it answers ---------------------------------------

    public function test_the_consultation_exposes_investigation_ids_for_uploads(): void
    {
        $this->asRole('doctor');

        $response = $this->getJson('/api/consultations/V-002')->assertOk();

        $investigations = $response->json('investigations');
        $this->assertNotEmpty($investigations, 'Uploads need database ids to attach to.');
        $this->assertArrayHasKey('id', $investigations[0]);
        $this->assertArrayHasKey('name', $investigations[0]);
    }

    public function test_a_report_cannot_be_filed_against_another_patients_test(): void
    {
        $this->asRole('doctor');

        $foreign = Investigation::where('visit_id', 'V-002')->firstOrFail();

        // P-002's report, P-001's test. Accepting this would attach one patient's result to
        // another patient's chart.
        $this->postJson('/api/patients/P-002/reports', [
            'investigation_id' => $foreign->id,
        ])->assertStatus(422);
    }

    // --- vitals belong to an attendance, not to the patient ---------------------------

    public function test_a_new_arrival_starts_with_no_vitals(): void
    {
        $this->asRole('nurse');

        $first = WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'completed', 'arrived_at' => now()->subWeek(),
            'spo2' => 88, 'heart_rate' => 120, 'vitals_taken_at' => now()->subWeek(),
        ]);

        $entry = $this->postJson('/api/waiting-room', ['patient_id' => 'P-002'])
            ->assertCreated()->json('entry');

        $this->assertNotSame($first->id, $entry['id']);
        $this->assertNull($entry['vitals']['spo2'], 'A fresh attendance is measured again.');
        $this->assertFalse($entry['vitals_recorded']);
    }

    public function test_last_weeks_vitals_are_not_offered_to_a_new_consultation(): void
    {
        // The dangerous case: a stale reading prefilled into today's findings looks like a
        // measurement, which is worse than an empty field.
        WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'completed', 'arrived_at' => now()->subWeek(),
            'spo2' => 88, 'vitals_taken_at' => now()->subWeek(),
        ]);

        $this->asRole('doctor');

        $this->getJson('/api/patients/P-002/triage-vitals')
            ->assertOk()
            ->assertJsonPath('recorded', false)
            ->assertJsonPath('entry_id', null);
    }

    public function test_completing_a_visit_closes_the_attendance(): void
    {
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'visit_id' => 'V-002', 'status' => 'in_consultation',
            'arrived_at' => now(), 'spo2' => 91, 'vitals_taken_at' => now(),
        ]);

        $this->asRole('doctor');

        // Straight to the end of the workflow for V-002, which is WAITING_FOR_TESTS.
        $this->postJson('/api/consultations/V-002/results', ['summary' => 'All back.'])->assertOk();
        $this->postJson('/api/consultations/V-002/treat')->assertOk();
        $this->postJson('/api/consultations/V-002/complete')->assertOk();

        $this->assertSame('completed', $entry->fresh()->status);

        // And with the attendance closed, nothing is offered next time.
        $this->getJson('/api/patients/P-001/triage-vitals')->assertJsonPath('recorded', false);
    }

    public function test_the_open_attendance_for_this_visit_is_the_one_offered(): void
    {
        // Same patient, two attendances: an older one for a different visit, and today's.
        WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'in_consultation', 'arrived_at' => now()->subDays(3),
            'spo2' => 99, 'vitals_taken_at' => now()->subDays(3),
        ]);
        WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'visit_id' => 'V-002', 'status' => 'waiting',
            'arrived_at' => now(), 'spo2' => 91, 'vitals_taken_at' => now(),
        ]);

        $this->asRole('doctor');

        $this->getJson('/api/patients/P-001/triage-vitals?visit_id=V-002')
            ->assertOk()
            // JSON renders 91.0 as 91, so compare loosely on the value that matters.
            ->assertJsonPath('vitals.spo2', fn ($v) => (float) $v === 91.0);
    }

    // --- staying in the room -------------------------------------------------------

    public function test_a_patient_being_seen_is_still_in_the_queue(): void
    {
        $this->asRole('nurse');
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'status' => 'in_consultation', 'arrived_at' => now(),
        ]);

        // Being seen is not the same as having left. Dropping the row the moment a
        // consultation opens makes the queue disagree with who is actually in the clinic.
        $this->getJson('/api/waiting-room')
            ->assertOk()
            ->assertJsonPath('entries.0.id', $entry->id)
            ->assertJsonPath('entries.0.status', 'in_consultation');
    }

    public function test_releasing_the_patient_closes_the_row_but_keeps_the_visit(): void
    {
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-001', 'visit_id' => 'V-002', 'status' => 'in_consultation',
            'arrived_at' => now(),
        ]);

        $this->asRole('doctor');
        $this->postJson('/api/consultations/V-002/leave')
            ->assertOk()
            ->assertJsonPath('released', true);

        $this->assertSame('completed', $entry->fresh()->status);
        // The encounter is untouched — one waiting on tests is still waiting.
        $this->assertDatabaseHas('visits', ['id' => 'V-002', 'status' => 'WAITING_FOR_TESTS']);
        $this->getJson('/api/waiting-room')->assertJsonCount(0, 'entries');
    }

    public function test_a_draft_consultation_is_remembered_on_the_queue_row(): void
    {
        $this->asRole('nurse');
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'waiting', 'arrived_at' => now(),
        ]);

        $this->asRole('doctor');
        $draftId = $this->postJson('/api/consultations', ['patient_id' => 'P-002'])
            ->json('visit.id');

        // A draft has no row in , so it cannot go in the constrained column. It is
        // held separately — without this the doctor steps out and comes back to a second
        // empty consultation, with the first one's findings stranded.
        $this->postJson("/api/waiting-room/{$entry->id}/seen", ['visit_id' => $draftId])
            ->assertOk()
            ->assertJsonPath('entry.active_visit_id', $draftId)
            ->assertJsonPath('entry.visit_id', null);

        $this->assertDatabaseHas('waiting_room_entries', [
            'id' => $entry->id, 'draft_visit_id' => $draftId, 'visit_id' => null,
        ]);
    }

    public function test_the_draft_pointer_graduates_when_the_visit_becomes_real(): void
    {
        $this->asRole('nurse');
        $entry = WaitingRoomEntry::create([
            'patient_id' => 'P-002', 'status' => 'waiting', 'arrived_at' => now(),
        ]);

        $this->asRole('doctor');
        $draftId = $this->postJson('/api/consultations', ['patient_id' => 'P-002'])->json('visit.id');
        $this->postJson("/api/waiting-room/{$entry->id}/seen", ['visit_id' => $draftId])->assertOk();

        $this->postJson("/api/consultations/{$draftId}/diagnosis", ['label' => 'Asthma exacerbation'])
            ->assertOk();

        // Now that the visit exists, the provisional pointer becomes the real foreign key.
        $this->assertDatabaseHas('waiting_room_entries', [
            'id' => $entry->id, 'visit_id' => $draftId, 'draft_visit_id' => null,
        ]);
    }
}
