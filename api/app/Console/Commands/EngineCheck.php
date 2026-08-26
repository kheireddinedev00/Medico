<?php

namespace App\Console\Commands;

use App\Engine\ChartBuilder;
use App\Engine\EngineClient;
use App\Engine\EngineContractException;
use App\Engine\EngineUnavailableException;
use App\Models\Patient;
use Illuminate\Console\Command;

/**
 * Verifies that this application and the clinical engine still agree on the contract.
 *
 * The failure this exists to catch is a quiet one. Both sides describe the same chart, in
 * two languages, and a column added here without a matching field there does not break
 * anything visibly — it produces a 422 on one endpoint, or worse, a field that silently
 * stops travelling. Running this after any change to a model or a serialiser turns that
 * into an immediate, readable failure.
 *
 *     php artisan engine:check
 */
class EngineCheck extends Command
{
    protected $signature = 'engine:check {patient? : Patient id to build a chart from}';

    protected $description = 'Check the clinical engine is reachable and the chart contract still holds';

    public function handle(): int
    {
        $engine = EngineClient::fromConfig();

        try {
            $health = $engine->health();
        } catch (EngineUnavailableException $e) {
            $this->error('The engine is not reachable at '.config('engine.url'));
            $this->line('Start it from the project root:');
            $this->line('  venv/Scripts/python -m uvicorn service.app:app --port 8001');

            return self::FAILURE;
        }

        $this->info('Engine is up.');
        $this->table(['setting', 'value'], [
            ['model', $health['model'] ?? '?'],
            ['ICD-10 mode', $health['icd10_mode'] ?? '?'],
            ['ICD-10 codes', $health['icd10_codes'] ?? 0],
            ['API key set', ($health['api_key_configured'] ?? false) ? 'yes' : 'no'],
        ]);

        // The engine owns the transition table; this only reports what it says, so a
        // mismatch shows up as a difference rather than being papered over.
        $workflow = $engine->workflow();
        $this->info('Workflow states: '.count($workflow['states'] ?? []));

        $patient = $this->argument('patient')
            ? Patient::find($this->argument('patient'))
            : Patient::has('visits')->first();

        if (! $patient) {
            $this->warn('No patient with a visit to test the chart contract against.');
            $this->line('Run: php artisan db:seed');

            return self::SUCCESS;
        }

        $visit = $patient->visits()->latest('created_at')->first();
        $chart = ChartBuilder::forVisit($visit);

        $this->line('');
        $this->info("Posting a chart for {$patient->full_name} ({$patient->id}), visit {$visit->id}...");

        try {
            // next-states is the cheapest round trip that still validates the whole chart:
            // the engine parses profile, visit, history and reports before it answers, and
            // no model is called, so this costs nothing and cannot rate-limit.
            $result = $engine->transition('next-states', ['chart' => $chart]);
        } catch (EngineContractException $e) {
            $this->error('The engine rejected the chart — the two schemas have drifted.');
            $this->line(json_encode($e->detail, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));

            return self::FAILURE;
        }

        $this->info('Chart accepted.');
        $this->line('  status: '.$result['status']);
        $this->line('  may move to: '.(implode(', ', $result['next']) ?: 'nothing (terminal)'));
        $this->line('');
        $this->line(sprintf(
            '  sent: %d allergies, %d medications, %d past visits, %d reports',
            count($chart['profile']['allergies']),
            count($chart['profile']['medications']),
            count($chart['history']),
            count($chart['reports']),
        ));

        return self::SUCCESS;
    }
}
