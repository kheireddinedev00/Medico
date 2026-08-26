<?php

namespace App\Http\Controllers;

use App\Engine\EngineClient;
use App\Models\Investigation;
use App\Models\Patient;
use App\Models\Report;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

/**
 * Uploading reports, and asking what they mean.
 *
 * The two are separate endpoints because they are separate acts, and the separation is load
 * bearing rather than cosmetic:
 *
 * **Upload transcribes.** The vision-language model reads what is printed and interprets
 * nothing. What it returns is written once, to `extracted`, and never modified — its value
 * is that months later it still says exactly what the page said.
 *
 * **Analysis interprets, and only when a physician asks.** It is stored beside the
 * transcription, never merged into it. Until someone runs it, `analysis` is null, and null
 * here is a clinical state — uploaded, but nobody has yet asked what it means — not a
 * loading placeholder. The engine refuses to reason over an unanalysed report for exactly
 * that reason.
 */
class ReportController extends Controller
{
    public function __construct(private readonly EngineClient $engine)
    {
    }

    public function index(Request $request, Patient $patient): JsonResponse
    {
        $this->authorisePatient($request, $patient);

        return response()->json([
            'reports' => $patient->reports()->with('investigation:id,name')
                ->orderByDesc('uploaded_at')->get()
                ->map(fn (Report $r) => [
                    'id' => $r->id,
                    'label' => $r->label(),
                    'kind' => $r->kind,
                    'report_type' => $r->report_type,
                    'report_date' => $r->report_date,
                    'visit_id' => $r->visit_id,
                    'investigation_id' => $r->investigation_id,
                    'investigation' => $r->investigation?->name,
                    'uploaded_at' => $r->uploaded_at,
                    'analysed' => $r->isAnalysed(),
                ]),
        ]);
    }

    public function show(Request $request, Report $report): JsonResponse
    {
        $this->authorisePatient($request, $report->patient);

        return response()->json([
            'report' => [
                'id' => $report->id,
                'label' => $report->label(),
                'kind' => $report->kind,
                'visit_id' => $report->visit_id,
                'uploaded_at' => $report->uploaded_at,
                // Kept apart in the response exactly as they are kept apart in the table.
                // A client that merged them would undo the guarantee at the last step.
                'extracted' => $report->extracted,
                'analysis' => $report->analysis,
                'analysed_at' => $report->analysed_at,
            ],
        ]);
    }

    /**
     * Upload and transcribe.
     *
     * Synchronous for now. A model call on a free tier can take a while, so this is the
     * first thing that should move to a queued job — the note in the roadmap, not a
     * surprise for whoever finds it slow.
     */
    public function store(Request $request, Patient $patient): JsonResponse
    {
        $data = $request->validate([
            'file' => ['required', 'file', 'mimes:pdf,png,jpg,jpeg,webp', 'max:20480'],
            // Nullable on purpose: results arrive late, out of order, and sometimes for
            // nothing anyone ordered.
            'visit_id' => ['nullable', 'string', 'exists:visits,id'],
            // Which ordered test this answers. Optional, because results arrive for things
            // nobody ordered — but when it is given, the outstanding list becomes real:
            // the doctor can see the chest X-ray is back and the culture is not.
            'investigation_id' => ['nullable', 'integer', 'exists:investigations,id'],
            'kind' => ['nullable', 'in:laboratory,radiology,other'],
        ]);

        $investigation = ! empty($data['investigation_id'])
            ? Investigation::findOrFail($data['investigation_id'])
            : null;

        if ($investigation) {
            abort_if(
                $investigation->visit->patient_id !== $patient->id,
                422,
                'That test belongs to a different patient.',
            );
        }

        // PHP's own execution timer, not the HTTP client's.
        //
        // Reading a scanned page takes about a minute on the free tier, and a PDF costs
        // roughly that per page — so this request legitimately outlives any general-purpose
        // limit. Left alone, PHP kills the process mid-call and the physician sees
        // "Maximum execution time exceeded" for work the model had nearly finished, with
        // the transcription lost and the upload needing to start again.
        //
        // Set here rather than in php.ini so it applies to this one slow endpoint instead of
        // letting every request in the application run unbounded.
        $this->allowLongRunningRequest();

        $file = $request->file('file');
        $path = $file->store('reports', 'local');
        $absolute = Storage::disk('local')->path($path);

        $extracted = $this->engine->extractReport($absolute, $file->getClientOriginalName());

        $document = $extracted['document'] ?? [];

        $report = Report::create([
            'id' => 'r-'.Str::lower(Str::random(12)),
            'patient_id' => $patient->id,
            // An investigation knows its visit, so uploading against a test files the
            // report on the right encounter without the client having to say so twice.
            'visit_id' => $investigation?->visit_id ?? ($data['visit_id'] ?? null),
            'investigation_id' => $investigation?->id,
            // Taken from the test that was ordered when there is one — the category was
            // already normalised in the engine when it was ordered.
            'kind' => $data['kind'] ?? ($investigation?->category ?? 'laboratory'),
            // As printed on the report, never inferred.
            'report_type' => $document['report_type'] ?? null,
            'report_date' => $document['report_date'] ?? null,
            'source_file' => $path,
            'extracted' => $extracted,
            'uploaded_by' => $request->user()->id,
            'uploaded_at' => now(),
        ]);

        $this->storeResultRows($report);

        Auditor::record($request->user()->id, 'report.uploaded', $report, null, [
            'file' => $file->getClientOriginalName(),
            'visit_id' => $report->visit_id,
        ], $request->ip());

        return response()->json([
            'report' => $report->fresh(),
            'message' => 'Transcribed. Nothing has been interpreted — run the analysis when you want it read.',
        ], 201);
    }

    /**
     * Interpret a stored transcription. The physician's explicit second step.
     *
     * Re-running is allowed and overwrites the previous interpretation. The transcription
     * underneath is untouched either way, so nothing that was read off the page can be lost
     * by re-reading it.
     */
    public function analyse(Request $request, Report $report): JsonResponse
    {
        // Faster than transcription — it reasons over text rather than an image — but still
        // a model call, and still capable of outliving a 60-second limit on a bad day.
        $this->allowLongRunningRequest();

        $analysis = $this->engine->analyseReport($report->extracted ?? []);

        $report->update([
            'analysis' => $analysis,
            'analysed_at' => now(),
            'analysed_by' => $request->user()->id,
        ]);

        Auditor::record($request->user()->id, 'report.analysed', $report, null, null, $request->ip());

        return response()->json([
            'report' => $report->fresh(),
            'analysis' => $analysis,
        ]);
    }

    /**
     * Flatten the transcribed results into rows, for charting a parameter over time.
     *
     * A convenience derived from `extracted`, never a replacement for it. Values are stored
     * as text because titres ("1:160"), thresholds ("<0.01") and qualitative results
     * ("Negative") are real, and a numeric column destroys them without saying so.
     */
    private function storeResultRows(Report $report): void
    {
        $results = $report->extracted['results'] ?? [];

        if (! $results) {
            return;
        }

        DB::transaction(function () use ($report, $results) {
            $report->results()->delete();

            foreach ($results as $result) {
                $range = $result['reference_range'] ?? [];

                $report->results()->create([
                    'parameter' => $result['parameter'] ?? 'unknown',
                    'value' => $this->asText($result['value'] ?? null),
                    'unit' => $result['unit'] ?? null,
                    'ref_low' => $this->asText($range['low'] ?? null),
                    'ref_high' => $this->asText($range['high'] ?? null),
                    'ref_text' => $range['text'] ?? null,
                    'status' => $result['status'] ?? null,
                    // The laboratory's own flag, as printed. It beats anything computed.
                    'flags' => $result['flags'] ?? null,
                ]);
            }
        });
    }

    private function asText(mixed $value): ?string
    {
        return $value === null ? null : (string) $value;
    }

    /**
     * Lift PHP's execution limit for a request that waits on the model.
     *
     * Matched to the engine's own extraction timeout plus a margin, so the HTTP client is
     * what gives up first and the failure arrives as a readable "the assistant is
     * unavailable" rather than PHP killing the process with a fatal error partway through.
     *
     * Guarded because `set_time_limit` is disabled on some hosts; where it is, this is a
     * no-op and the deployment needs a longer limit configured for the web server instead.
     */
    private function allowLongRunningRequest(): void
    {
        if (! function_exists('set_time_limit')) {
            return;
        }

        @set_time_limit((int) config('engine.extract_timeout', 600) + 60);
    }

    private function authorisePatient(Request $request, Patient $patient): void
    {
        $user = $request->user();

        abort_if(
            $user->role === 'patient' && $user->patient_id !== $patient->id,
            403,
            'You may only view your own reports.',
        );
    }
}
