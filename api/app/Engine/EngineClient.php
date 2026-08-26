<?php

namespace App\Engine;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;

/**
 * The only thing in this application that talks to the clinical engine.
 *
 * Everything clinical happens on the other side of this class: which state a visit may move
 * to, which drug is withheld for an allergy, which ICD-10 code is real, what the
 * differential is. This side asks and stores the answer.
 *
 * That is worth defending, because the temptation runs the other way. It will one day seem
 * easier to check `status === 'COMPLETED'` in PHP than to make a call — and the moment two
 * languages both decide what a valid transition is, they will eventually disagree, and the
 * disagreement will be found by a doctor rather than by a test.
 *
 * Errors are translated, not swallowed. A 409 from the engine is a real clinical refusal
 * and is re-thrown as one, so it reaches the physician as an explanation instead of a 500.
 */
class EngineClient
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly ?string $apiKey = null,
        private readonly int $timeout = 120,
        // Document reading gets its own, much longer budget. See config/engine.php.
        private readonly int $extractTimeout = 600,
    ) {
    }

    public static function fromConfig(): self
    {
        return new self(
            rtrim(config('engine.url'), '/'),
            config('engine.key') ?: null,
            (int) config('engine.timeout'),
            (int) config('engine.extract_timeout'),
        );
    }

    /** Is the engine up, and which mode is it running in? */
    public function health(): array
    {
        return $this->request('get', '/health');
    }

    /** The curated ICD-10 list, for seeding. One source, copied rather than retyped. */
    public function icd10Reference(): array
    {
        return $this->request('get', '/reference/icd10');
    }

    /**
     * The state machine.
     *
     * Served rather than hard-coded on this side so the UI can grey out what the engine
     * would refuse, without keeping a second copy of the transition table to drift against.
     */
    public function workflow(): array
    {
        return $this->request('get', '/reference/workflow');
    }

    // --- consultation -----------------------------------------------------------

    /**
     * Run one transition. Returns ['visit' => [...], 'persist' => bool].
     *
     * `persist` is the engine's answer to whether this visit belongs in the record yet —
     * false until a diagnosis is chosen, so an assessment the doctor abandons leaves
     * nothing behind. Honour it; do not second-guess it here.
     */
    public function transition(string $action, array $payload): array
    {
        return $this->request('post', "/consultation/{$action}", $payload);
    }

    public function startVisit(array $payload): array
    {
        return $this->request('post', '/consultation/start', $payload);
    }

    // --- the assistant ----------------------------------------------------------

    public function assess(array $chart): array
    {
        return $this->request('post', '/assistant/assess', ['chart' => $chart]);
    }

    public function suggestCodes(array $chart): array
    {
        return $this->request('post', '/assistant/icd10', ['chart' => $chart]);
    }

    public function suggestInvestigations(array $chart): array
    {
        return $this->request('post', '/assistant/investigations', ['chart' => $chart]);
    }

    /**
     * Treatment options, already screened against the patient's record.
     *
     * There is no unscreened variant of this call, by design — the screen runs inside the
     * engine's session, so no caller can obtain raw suggestions by accident.
     */
    public function suggestMedications(array $chart): array
    {
        return $this->request('post', '/assistant/medications', ['chart' => $chart]);
    }

    /**
     * Screen drugs the physician named themselves.
     *
     * Same code screen as the suggestions go through, pointed at a typed list. Nothing is
     * removed — a physician's choice is not overruled by a screen — but they are told what
     * it found.
     */
    public function checkMedications(array $chart, array $names): array
    {
        return $this->request('post', '/assistant/check-medications', [
            'chart' => $chart,
            'names' => $names,
        ]);
    }

    // --- reports ----------------------------------------------------------------

    /** Transcribe an uploaded report. Interprets nothing. */
    public function extractReport(string $absolutePath, string $filename): array
    {
        try {
            $response = Http::timeout($this->extractTimeout)
                ->withHeaders($this->headers())
                ->attach('file', file_get_contents($absolutePath), $filename)
                ->post($this->baseUrl.'/reports/extract');
        } catch (ConnectionException $e) {
            throw new EngineUnavailableException(
                "The clinical engine is not reachable at {$this->baseUrl} ({$e->getMessage()})."
            );
        }

        return $this->handle($response, '/reports/extract');
    }

    /** Interpret a stored extraction — the physician's explicit second step. */
    public function analyseReport(array $extracted): array
    {
        return $this->request('post', '/reports/analyze', ['extracted' => $extracted]);
    }

    // --- SOAP -------------------------------------------------------------------

    public function soap(array $chart, bool $includeAssistantDifferential = true): array
    {
        return $this->request('post', '/soap', [
            'chart' => $chart,
            'include_assistant_differential' => $includeAssistantDifferential,
        ]);
    }

    // --- internals --------------------------------------------------------------

    private function headers(): array
    {
        return $this->apiKey ? ['X-Service-Key' => $this->apiKey] : [];
    }

    private function request(string $method, string $path, array $payload = []): array
    {
        $http = Http::timeout($this->timeout)
            ->withHeaders($this->headers())
            ->acceptJson();

        try {
            $response = $method === 'get'
                ? $http->get($this->baseUrl.$path)
                : $http->post($this->baseUrl.$path, $payload);
        } catch (ConnectionException $e) {
            // The engine is down or unreachable. Guzzle throws before there is a response
            // to inspect, so this never reaches the status mapping below — and without
            // catching it, "the assistant is not running" surfaces as a raw cURL trace.
            throw new EngineUnavailableException(
                "The clinical engine is not reachable at {$this->baseUrl} ({$e->getMessage()})."
            );
        }

        return $this->handle($response, $path);
    }

    private function handle($response, string $path): array
    {
        if ($response->successful()) {
            return $response->json() ?? [];
        }

        $body = $response->json() ?? [];

        // 409 is the engine refusing on clinical grounds: a transition the workflow does
        // not allow, a diagnosis revised at the wrong point, a completed visit being
        // reopened. The detail is written for a clinician, so it travels intact.
        if ($response->status() === 409) {
            throw new EngineRefusedException(
                $body['detail'] ?? 'The engine refused that step.',
                $body['error'] ?? 'consultation_error',
            );
        }

        // 422 means this application sent something the engine's models reject — a field
        // it does not know, or one it does and we got wrong. That is our bug, not the
        // clinician's, and the validation detail is the fastest way to find it.
        if ($response->status() === 422) {
            throw new EngineContractException(
                "The engine rejected the payload for {$path}.",
                $body['detail'] ?? $body,
            );
        }

        throw new EngineUnavailableException(
            "Engine call to {$path} failed with HTTP {$response->status()}: ".$response->body()
        );
    }
}
