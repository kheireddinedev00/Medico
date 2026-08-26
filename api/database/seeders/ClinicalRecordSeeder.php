<?php

namespace Database\Seeders;

use App\Models\Icd10Code;
use App\Models\Patient;
use App\Models\User;
use App\Models\Visit;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\Hash;

/**
 * Loads the fixture record from the engine's own data files.
 *
 * The files in `data/` are read directly rather than through the HTTP service, so seeding
 * works before the engine is running and during CI. They remain the master copy — this
 * database is a loaded copy of them, exactly as the engine's own SQLite store is.
 *
 * Read them, do not edit them from here. Anything written back into `data/seed/patients.json`
 * would diverge from what the CLI loads, and then there would be two fixture sets.
 */
class ClinicalRecordSeeder extends Seeder
{
    public function run(): void
    {
        $this->seedIcd10Codes();
        $this->seedUsers();
        $this->seedPatientsAndVisits();
    }

    private function enginePath(string $relative): string
    {
        return base_path('../'.$relative);
    }

    private function readJson(string $relative): array
    {
        $path = $this->enginePath($relative);

        if (! is_file($path)) {
            $this->command?->warn("Skipped: {$relative} not found at {$path}");

            return [];
        }

        return json_decode(file_get_contents($path), true) ?? [];
    }

    /**
     * The curated respiratory subset, copied from the engine's list.
     *
     * With this populated the assistant's codes are validated against it rather than merely
     * shape-checked, and the description a physician sees comes from here rather than from
     * the model.
     */
    private function seedIcd10Codes(): void
    {
        $data = $this->readJson('data/icd10_respiratory.json');
        $codes = $data['codes'] ?? [];

        foreach (array_chunk($codes, 200) as $chunk) {
            Icd10Code::upsert(
                array_map(fn ($entry) => [
                    'code' => $entry['code'],
                    'description' => $entry['description'],
                    'synonyms' => json_encode($entry['synonyms'] ?? []),
                    'created_at' => now(),
                    'updated_at' => now(),
                ], $chunk),
                ['code'],
                ['description', 'synonyms', 'updated_at'],
            );
        }

        $this->command?->info('ICD-10 codes: '.count($codes));
    }

    /**
     * Demo accounts. Development only — the passwords are in the repository.
     */
    private function seedUsers(): void
    {
        $accounts = [
            ['name' => 'Dr. Amina Belkacem', 'email' => 'doctor@clinic.test', 'role' => User::ROLE_DOCTOR],
            ['name' => 'Nurse Sofiane Haddad', 'email' => 'nurse@clinic.test', 'role' => User::ROLE_NURSE],
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
        $data = $this->readJson('data/seed/patients.json');

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
