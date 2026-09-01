<?php

namespace Database\Seeders;

use App\Models\Patient;
use App\Models\User;
use App\Models\Visit;
use Database\Seeders\Concerns\ReadsSeedFiles;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\Hash;

/**
 * Loads the fixture record from the backend's own seed files.
 *
 * The files live in `database/seed/`, inside this application. That is deliberate: seeding
 * reaches for nothing outside `api/`, so the backend can be deployed, zipped or checked out
 * on its own and `php artisan db:seed` still works with no other service running.
 *
 * `patients.json` is the master copy — it lives here and nowhere else.
 *
 * The ICD-10 list is loaded by `Icd10CodeSeeder`, which is separate so that curating a code
 * does not require running this one — everything below resets the fixture patients to their
 * file state, and a doctor's own edits to those charts would go with it.
 */
class ClinicalRecordSeeder extends Seeder
{
    use ReadsSeedFiles;

    public function run(): void
    {
        $this->call(Icd10CodeSeeder::class);
        $this->seedUsers();
        $this->seedPatientsAndVisits();
    }

    private function seedUsers(): void
    {
        $accounts = [
            ['name' => 'Dr. Amina Belkacem', 'email' => 'doctor@clinic.test', 'role' => User::ROLE_DOCTOR],
            // A second doctor, so assignment is something you can actually see working —
            // with one doctor, "only my patients" and "all patients" look identical.
            ['name' => 'Dr. Yacine Meddour', 'email' => 'doctor2@clinic.test', 'role' => User::ROLE_DOCTOR],
            ['name' => 'Nurse Sofiane Haddad', 'email' => 'nurse@clinic.test', 'role' => User::ROLE_NURSE],
            // Two nurses, because the queue is shared and that is worth demonstrating.
            ['name' => 'Nurse Yasmine Kaci', 'email' => 'nurse2@clinic.test', 'role' => User::ROLE_NURSE],
            ['name' => 'Clinic Administrator', 'email' => 'admin@clinic.test', 'role' => User::ROLE_ADMIN],
        ];

        foreach ($accounts as $account) {
            User::updateOrCreate(
                ['email' => $account['email']],
                $account + ['password' => Hash::make('password'), 'is_active' => true],
            );
        }

        $this->command?->info('Users: '.count($accounts).' (password: "password")');
    }

    private function seedPatientsAndVisits(): void
    {
        $data = $this->readJson('patients.json');

        foreach ($data['patients'] ?? [] as $record) {
            $smoking = $record['smoking'] ?? [];

            $patient = Patient::updateOrCreate(['id' => $record['id']], [
                'full_name' => $record['full_name'],
                'sex' => $record['sex'] ?? 'unknown',
                'date_of_birth' => $record['date_of_birth'] ?? null,
                'age_years' => $record['age_years'] ?? null,
                'smoking_status' => $smoking['status'] ?? 'unknown',
                'pack_years' => $smoking['pack_years'] ?? null,
                'quit_year' => $smoking['quit_year'] ?? null,
                'notes' => $record['notes'] ?? null,
            ]);

            // Replaced wholesale so re-running the seeder is idempotent rather than
            // additive — a seeder that doubles the allergy list on second run is worse
            // than useless, because the duplicate reads as corroboration.
            $patient->allergies()->delete();
            foreach ($record['allergies'] ?? [] as $allergy) {
                $patient->allergies()->create([
                    'substance' => $allergy['substance'],
                    'reaction' => $allergy['reaction'] ?? null,
                    'severity' => $allergy['severity'] ?? 'unknown',
                ]);
            }

            $patient->medications()->delete();
            foreach ($record['medications'] ?? [] as $medication) {
                $patient->medications()->create([
                    'name' => $medication['name'],
                    'dose' => $medication['dose'] ?? null,
                    'frequency' => $medication['frequency'] ?? null,
                    'indication' => $medication['indication'] ?? null,
                    'started' => $medication['started'] ?? null,
                    'active' => $medication['active'] ?? true,
                ]);
            }

            $patient->chronicConditions()->delete();
            foreach ($record['chronic_conditions'] ?? [] as $condition) {
                $patient->chronicConditions()->create([
                    'name' => $condition['name'],
                    'since' => $condition['since'] ?? null,
                    'notes' => $condition['notes'] ?? null,
                ]);
            }
        }

        foreach ($data['visits'] ?? [] as $record) {
            $vitals = $record['vitals'] ?? [];
            $diagnosis = $record['working_diagnosis'] ?? null;

            $visit = Visit::updateOrCreate(['id' => $record['id']], [
                'patient_id' => $record['patient_id'],
                'status' => $record['status'] ?? 'INITIAL_ASSESSMENT',
                'chief_complaint' => $record['chief_complaint'] ?? null,
                'symptoms' => $record['symptoms'] ?? [],
                'physical_exam' => $record['physical_exam'] ?? null,
                'observations' => $record['observations'] ?? null,
                'results_summary' => $record['results_summary'] ?? null,
                'doctor_notes' => $record['doctor_notes'] ?? null,
                'temperature_c' => $vitals['temperature_c'] ?? null,
                'heart_rate' => $vitals['heart_rate'] ?? null,
                'respiratory_rate' => $vitals['respiratory_rate'] ?? null,
                'blood_pressure' => $vitals['blood_pressure'] ?? null,
                'spo2' => $vitals['spo2'] ?? null,
                'weight_kg' => $vitals['weight_kg'] ?? null,
                'working_diagnosis_label' => $diagnosis['label'] ?? null,
                'working_diagnosis_icd10' => $diagnosis['icd10_code'] ?? null,
                'working_diagnosis_reasoning' => $diagnosis['reasoning'] ?? null,
            ]);

            if (! empty($record['created_at'])) {
                // Set directly: the timeline is the point of the fixture, and letting
                // Eloquent stamp today's date would collapse three encounters onto one day.
                $visit->forceFill(['created_at' => $record['created_at']])->saveQuietly();
            }

            $visit->investigations()->delete();
            foreach ($record['ordered_investigations'] ?? [] as $investigation) {
                $visit->investigations()->create([
                    'name' => $investigation['name'],
                    'category' => $investigation['category'] ?? 'other',
                    'rationale' => $investigation['rationale'] ?? null,
                    'status' => $investigation['status'] ?? 'ordered',
                ]);
            }

            $visit->prescriptions()->delete();
            foreach ($record['prescribed_medications'] ?? [] as $medication) {
                $visit->prescriptions()->create([
                    'name' => $medication['name'],
                    'dose' => $medication['dose'] ?? null,
                    'frequency' => $medication['frequency'] ?? null,
                    'duration' => $medication['duration'] ?? null,
                    'rationale' => $medication['rationale'] ?? null,
                ]);
            }
        }

        $this->command?->info('Patients: '.count($data['patients'] ?? []).', visits: '.count($data['visits'] ?? []));
    }
}
