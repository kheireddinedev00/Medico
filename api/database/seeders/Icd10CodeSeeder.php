<?php

namespace Database\Seeders;

use App\Models\Icd10Code;
use Database\Seeders\Concerns\ReadsSeedFiles;
use Illuminate\Database\Seeder;

/**
 * The curated respiratory code list, loaded into the table a physician searches.
 *
 * Its own seeder because the list changes on its own schedule. Curating one new code should
 * not mean running the whole record seeder, which resets the fixture patients to their file
 * state — a doctor's own edits to those charts would go with it. Run just this:
 *
 *     php artisan db:seed --class=Icd10CodeSeeder
 *
 * Safe to run at any time and as often as you like: it is an upsert keyed on the code, so
 * it adds what is new, refreshes what changed, and touches nothing else in the database.
 *
 * It does not remove codes. A code deleted from the file stays in the table, because
 * deleting one that a past visit was coded with would leave that diagnosis unreadable.
 *
 * With this populated the assistant's codes are validated against the list rather than
 * merely shape-checked, and the description a physician sees comes from here rather than
 * from the model.
 */
class Icd10CodeSeeder extends Seeder
{
    use ReadsSeedFiles;

    public function run(): void
    {
        $codes = $this->readJson('icd10_respiratory.json')['codes'] ?? [];

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
}
