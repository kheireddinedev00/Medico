<?php

namespace Tests\Feature;

use App\Models\Patient;
use App\Models\User;
use App\Models\WaitingRoomEntry;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * The triage agent, as the waiting room uses it.
 *
 * These tests run against the live engine, like the consultation tests do — the point is
 * the integration, and a mocked engine would only prove that the mock matches what this
 * side expects, which is exactly the assumption that breaks. They run with the model
 * turned off (see phpunit.xml), so the deterministic layers decide and the results are
 * the same on every run.
 *
 * What is being defended here is not the scoring — `tests/test_news2.py` does that — but
 * the two properties this side is responsible for:
 *
 *   1. An unscored patient is never treated as a low-priority one.
 *   2. The nurse's decision and the agent's both survive, separately.
 */
class TriageTest extends TestCase
{
    use RefreshDatabase;

    private function nurse(): User
    {
        return User::factory()->create(['role' => 'nurse']);
    }

    private function patient(array $attributes = []): Patient
    {
        return Patient::create(array_merge([
            'id' => 'P-'.fake()->unique()->numerify('###'),
            'full_name' => 'Test Patient',
            'sex' => 'male',
            'age_years' => 58,
        ], $attributes));
    }

    private function queue(Patient $patient): WaitingRoomEntry
    {
        return WaitingRoomEntry::create([
            'patient_id' => $patient->id,
            'arrived_at' => now(),
            'status' => 'waiting',
        ]);
    }

    /** The vitals form is also the triage form; one save does both. */
    public function test_recording_vitals_scores_the_patient(): void
    {
        $entry = $this->queue($this->patient());

        $response = $this->actingAs($this->nurse())
            ->postJson("/api/waiting-room/{$entry->id}/vitals", [
                'chief_complaint' => 'Cough for four days',
                'temperature_c' => 37.4, 'heart_rate' => 84, 'respiratory_rate' => 16,
                'blood_pressure' => '122/78', 'spo2' => 96,
                'on_oxygen' => false, 'consciousness' => 'alert',
            ])
            ->assertOk();

        $response->assertJsonPath('triaged', true);
        $response->assertJsonPath('entry.triage.scored', true);
        $response->assertJsonPath('entry.triage.priority', 'LOW');
        $response->assertJsonPath('entry.triage.priority_rank', 0);
    }

    /**
     * The case the whole red-flag layer exists for.
     *
     * Every observation is normal — NEWS2 scores zero — and the patient is describing an
     * acute coronary syndrome. If this ever comes back as anything but CRITICAL, the
     * integration has stopped sending the complaint.
     */
    public function test_a_normal_set_of_vitals_with_chest_pain_is_critical(): void
    {
        $entry = $this->queue($this->patient());

        $this->actingAs($this->nurse())
            ->postJson("/api/waiting-room/{$entry->id}/vitals", [
                'chief_complaint' => 'Crushing central chest pressure with sweating',
                'temperature_c' => 36.8, 'heart_rate' => 78, 'respiratory_rate' => 16,
                'blood_pressure' => '128/76', 'spo2' => 98,
                'on_oxygen' => false, 'consciousness' => 'alert',
            ])
            ->assertOk()
            ->assertJsonPath('entry.triage.priority', 'CRITICAL')
            ->assertJsonPath('entry.triage.priority_rank', 3)
            ->assertJsonPath('entry.triage.news2.aggregate', 0);
    }

    /** The queue is ordered by the engine's rank, then by who arrived first. */
    public function test_the_queue_is_ordered_by_priority_then_arrival(): void
    {
        $nurse = $this->nurse();

        $cases = [
            ['complaint' => 'Repeat prescription', 'spo2' => 98, 'rr' => 16, 'hr' => 74],
            ['complaint' => 'Crushing chest pain', 'spo2' => 98, 'rr' => 16, 'hr' => 74],
            ['complaint' => 'Cough', 'spo2' => 96, 'rr' => 26, 'hr' => 84],
        ];

        foreach ($cases as $i => $case) {
            $entry = $this->queue($this->patient());
            // Distinct arrival times so the within-priority ordering is unambiguous.
            $entry->update(['arrived_at' => now()->subMinutes(30 - $i)]);

            $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
                'chief_complaint' => $case['complaint'],
                'temperature_c' => 36.8, 'heart_rate' => $case['hr'],
                'respiratory_rate' => $case['rr'], 'blood_pressure' => '122/78',
                'spo2' => $case['spo2'], 'on_oxygen' => false, 'consciousness' => 'alert',
            ])->assertOk();
        }

        $priorities = collect($this->actingAs($nurse)->getJson('/api/waiting-room')->json('entries'))
            ->pluck('triage.priority')
            ->all();

        $this->assertSame(['CRITICAL', 'URGENT', 'LOW'], $priorities);
    }

    /**
     * Sorting by priority can be turned off, and the priorities stay.
     *
     * The switch changes the order and nothing else. Every row still carries its
     * priority, its reasoning and its rank — which is what makes offering the switch
     * defensible: nobody loses clinical information by using it, they only lose the
     * sickest patient being at the top.
     */
    public function test_the_queue_can_be_ordered_by_arrival_instead(): void
    {
        $nurse = $this->nurse();

        // The urgent patient arrives second, so the two orderings disagree.
        $routine = $this->queue($this->patient());
        $routine->update(['arrived_at' => now()->subMinutes(40)]);

        $urgent = $this->queue($this->patient());
        $urgent->update(['arrived_at' => now()->subMinutes(10)]);

        foreach ([[$routine, 'Repeat prescription', 16], [$urgent, 'Crushing chest pain', 16]] as [$entry, $complaint, $rr]) {
            $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
                'chief_complaint' => $complaint,
                'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => $rr,
                'blood_pressure' => '122/78', 'spo2' => 98,
                'on_oxygen' => false, 'consciousness' => 'alert',
            ])->assertOk();
        }

        // By priority: the later arrival with chest pain comes first.
        $byPriority = $this->actingAs($nurse)->getJson('/api/waiting-room?order=priority');
        $byPriority->assertJsonPath('order', 'priority');
        $this->assertSame(
            [$urgent->id, $routine->id],
            collect($byPriority->json('entries'))->pluck('id')->all(),
        );

        // By arrival: strictly who came first.
        $byArrival = $this->actingAs($nurse)->getJson('/api/waiting-room?order=arrival');
        $byArrival->assertJsonPath('order', 'arrival');
        $this->assertSame(
            [$routine->id, $urgent->id],
            collect($byArrival->json('entries'))->pluck('id')->all(),
        );

        // The priority is still on every row — the sort changed, not the assessment.
        $byArrival->assertJsonPath('entries.1.triage.priority', 'CRITICAL');
        $byArrival->assertJsonPath('entries.1.triage.priority_rank', 3);
    }

    /** Priority ordering is the default, and a bad value falls back to it rather than off. */
    public function test_priority_is_the_default_and_the_fallback(): void
    {
        $nurse = $this->nurse();

        $this->actingAs($nurse)->getJson('/api/waiting-room')
            ->assertJsonPath('order', 'priority');

        $this->actingAs($nurse)->getJson('/api/waiting-room?order=nonsense')
            ->assertJsonPath('order', 'priority');
    }

    /** The counts drive the warning shown when urgent patients are not sorted to the top. */
    public function test_the_queue_reports_how_many_are_waiting_at_each_priority(): void
    {
        $nurse = $this->nurse();

        $critical = $this->queue($this->patient());
        $this->actingAs($nurse)->postJson("/api/waiting-room/{$critical->id}/vitals", [
            'chief_complaint' => 'Crushing chest pain',
            'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => 16,
            'blood_pressure' => '122/78', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        // Never measured, so never scored.
        $this->queue($this->patient());

        $this->actingAs($nurse)->getJson('/api/waiting-room')
            ->assertJsonPath('counts.CRITICAL', 1)
            ->assertJsonPath('counts.unscored', 1)
            ->assertJsonPath('counts.LOW', 0);
    }

    /**
     * An unmeasured patient sorts last but is not called low priority.
     *
     * The distinction is the point: LOW is a finding, and "nobody has looked at them" is
     * the absence of one.
     */
    public function test_an_unscored_patient_sorts_last_and_is_marked_unscored(): void
    {
        $nurse = $this->nurse();

        // Arrived first, never measured.
        $unscored = $this->queue($this->patient());
        $unscored->update(['arrived_at' => now()->subHour()]);

        $scored = $this->queue($this->patient());
        $this->actingAs($nurse)->postJson("/api/waiting-room/{$scored->id}/vitals", [
            'chief_complaint' => 'Repeat prescription',
            'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => 16,
            'blood_pressure' => '122/78', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $entries = $this->actingAs($nurse)->getJson('/api/waiting-room')->json('entries');

        $this->assertSame($scored->id, $entries[0]['id'], 'the scored patient should be first');
        $this->assertFalse($entries[1]['triage']['scored']);
        $this->assertNull($entries[1]['triage']['priority']);
    }

    /** Incomplete observations must never come back as LOW. */
    public function test_incomplete_observations_are_not_ranked_low(): void
    {
        $entry = $this->queue($this->patient());

        $this->actingAs($this->nurse())
            ->postJson("/api/waiting-room/{$entry->id}/vitals", [
                'chief_complaint' => 'Feeling tired',
                'temperature_c' => 37.2,
            ])
            ->assertOk()
            ->assertJsonPath('entry.triage.status', 'INSUFFICIENT_DATA');

        $this->assertGreaterThan(0, $entry->fresh()->suggested_priority);
    }

    // --- the override -----------------------------------------------------------

    public function test_a_nurse_can_override_the_priority_and_both_survive(): void
    {
        $nurse = $this->nurse();
        $entry = $this->queue($this->patient());

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
            'chief_complaint' => 'Repeat prescription',
            'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => 16,
            'blood_pressure' => '122/78', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $this->actingAs($nurse)
            ->postJson("/api/waiting-room/{$entry->id}/priority", [
                'priority' => 'URGENT',
                'reason' => 'Looks unwell despite the numbers; clammy and quiet.',
            ])
            ->assertOk()
            // The queue sorts on the nurse's call...
            ->assertJsonPath('entry.triage.priority', 'URGENT')
            ->assertJsonPath('entry.triage.priority_rank', 2)
            // ...and the agent's answer is still there to be compared with.
            ->assertJsonPath('entry.triage.suggested_priority', 'LOW')
            ->assertJsonPath('entry.triage.override.reason', 'Looks unwell despite the numbers; clammy and quiet.');
    }

    public function test_an_override_without_a_reason_is_refused(): void
    {
        $entry = $this->queue($this->patient());

        $this->actingAs($this->nurse())
            ->postJson("/api/waiting-room/{$entry->id}/priority", ['priority' => 'CRITICAL'])
            ->assertStatus(422);
    }

    public function test_clearing_an_override_returns_to_the_agents_priority(): void
    {
        $nurse = $this->nurse();
        $entry = $this->queue($this->patient());

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
            'chief_complaint' => 'Repeat prescription',
            'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => 16,
            'blood_pressure' => '122/78', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/priority", [
            'priority' => 'CRITICAL', 'reason' => 'Changed my mind about this one.',
        ])->assertOk();

        $this->actingAs($nurse)
            ->deleteJson("/api/waiting-room/{$entry->id}/priority")
            ->assertOk()
            ->assertJsonPath('entry.triage.priority', 'LOW')
            ->assertJsonPath('entry.triage.override', null);
    }

    /** A doctor who disagrees must be able to say so without going through a nurse. */
    public function test_a_doctor_can_override_the_priority(): void
    {
        $entry = $this->queue($this->patient());
        $doctor = User::factory()->create(['role' => 'doctor']);

        $this->actingAs($doctor)
            ->postJson("/api/waiting-room/{$entry->id}/priority", [
                'priority' => 'CRITICAL',
                'reason' => 'Saw them in the corridor; they are peri-arrest.',
            ])
            ->assertOk();
    }

    public function test_a_patient_account_cannot_override_a_priority(): void
    {
        $entry = $this->queue($this->patient());
        $patientUser = User::factory()->create(['role' => 'patient']);

        $this->actingAs($patientUser)
            ->postJson("/api/waiting-room/{$entry->id}/priority", [
                'priority' => 'CRITICAL', 'reason' => 'I have been waiting a long time.',
            ])
            ->assertForbidden();
    }

    /** Overriding the machine is a clinical act and is written down as one. */
    public function test_an_override_is_audited_with_both_priorities(): void
    {
        $nurse = $this->nurse();
        $entry = $this->queue($this->patient());

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
            'chief_complaint' => 'Repeat prescription',
            'temperature_c' => 36.8, 'heart_rate' => 74, 'respiratory_rate' => 16,
            'blood_pressure' => '122/78', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/priority", [
            'priority' => 'URGENT', 'reason' => 'Clinical judgement.',
        ])->assertOk();

        $this->assertDatabaseHas('audit_logs', ['action' => 'waiting_room.triaged']);
        $this->assertDatabaseHas('audit_logs', ['action' => 'waiting_room.priority_override']);
    }

    // --- re-triage --------------------------------------------------------------

    public function test_retriage_is_refused_when_nothing_has_been_measured(): void
    {
        $entry = $this->queue($this->patient());

        $this->actingAs($this->nurse())
            ->postJson("/api/waiting-room/{$entry->id}/retriage")
            ->assertStatus(422);
    }

    /**
     * Re-scoring unchanged observations returns the same priority.
     *
     * Worth asserting because it is the thing people assume is false: the queue does not
     * drift on its own, and a patient's priority only moves when someone measures them
     * again.
     */
    public function test_retriage_on_unchanged_observations_is_stable(): void
    {
        $nurse = $this->nurse();
        $entry = $this->queue($this->patient());

        $this->actingAs($nurse)->postJson("/api/waiting-room/{$entry->id}/vitals", [
            'chief_complaint' => 'Cough',
            'temperature_c' => 37.4, 'heart_rate' => 84, 'respiratory_rate' => 26,
            'blood_pressure' => '122/78', 'spo2' => 96,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $before = $entry->fresh()->suggested_priority_label;

        $this->actingAs($nurse)
            ->postJson("/api/waiting-room/{$entry->id}/retriage")
            ->assertOk()
            ->assertJsonPath('entry.triage.priority', $before);
    }

    // --- the record -------------------------------------------------------------

    /** The reasoning is stored, not just the badge. A priority nobody can explain is not one. */
    public function test_the_whole_decision_is_kept_for_review(): void
    {
        $entry = $this->queue($this->patient());

        $this->actingAs($this->nurse())->postJson("/api/waiting-room/{$entry->id}/vitals", [
            'chief_complaint' => 'Crushing chest pain',
            'temperature_c' => 36.8, 'heart_rate' => 78, 'respiratory_rate' => 16,
            'blood_pressure' => '128/76', 'spo2' => 98,
            'on_oxygen' => false, 'consciousness' => 'alert',
        ])->assertOk();

        $stored = $entry->fresh()->triage_result;

        $this->assertNotEmpty($stored['reasons']);
        $this->assertNotEmpty($stored['red_flags']);
        $this->assertTrue($stored['requires_human_review']);
        $this->assertNotEmpty($stored['ruleset_version']);
        $this->assertNotEmpty($entry->fresh()->triage_ruleset_version);
    }
}
