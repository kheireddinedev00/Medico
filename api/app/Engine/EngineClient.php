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

    // --- triage -----------------------------------------------------------------

    /**
     * Score one patient at the front desk.
     *
     * `$profile` is the chart when the patient is registered and null for a walk-in. The
     * engine is built to work either way; do not fabricate a profile to satisfy it.
     *
     * `$rulesOnly` skips the model. The deterministic layers produce a complete decision
     * without it, so this is the switch to reach for when the queue needs a guaranteed
     * fast answer rather than the best one.
     *
     * The response always contains a priority. A model that is down is reported inside
     * the result as `interpretation.available = false`, not as a failed call — so the
     * only thing this can throw is the engine itself being unreachable, or the rule set
     * being broken, and both of those are real outages worth surfacing.
     */
    public function triage(array $request, ?array $profile = null, bool $rulesOnly = false): array
    {
        return $this->request('post', '/triage/assess', [
            'request' => $request,
            'profile' => $profile,
            'rules_only' => $rulesOnly,
        ]);
    }

    /**
     * The rule set behind the priorities: the ladder, the actions, the red-flag list.
     *
     * Served rather than restated in PHP or React for the same reason `workflow()` is. A
     * screen that explains a priority using its own copy of the rules is a screen that
     * will eventually explain a decision the engine did not make.
     */
    public function triageRules(): array
    {
        return $this->request('get', '/reference/triage-rules');
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

    // --- reference library --------------------------------------------------------

    /** Every document the assistant can retrieve from, curated and physician-added. */
    public function references(): array
    {
        return $this->request('get', '/references');
    }

    /**
     * Add one document to the library and embed it.
     *
     * Given the long timeout because embedding happens inline: a document reported as
     * added but not yet retrievable would be a lie the interface has to keep. The
     * physician waits, and when the call returns the assistant can already cite it.
     */
    public function addReference(string $absolutePath, string $filename, array $meta): array
    {
        try {
            $request = Http::timeout($this->extractTimeout)
                ->withHeaders($this->headers())
                ->attach('file', file_get_contents($absolutePath), $filename);

            // Only what was actually filled in. Sending `year=` empty would fail the
            // engine's int parsing, where omitting it means "not stated".
            foreach (array_filter($meta, fn ($v) => $v !== null && $v !== '') as $key => $value) {
                $request = $request->attach($key, (string) $value);
            }

            $response = $request->post($this->baseUrl.'/references');
        } catch (ConnectionException $e) {
            throw new EngineUnavailableException(
                "The clinical engine is not reachable at {$this->baseUrl} ({$e->getMessage()})."
            );
        }

        return $this->handleDocument($response, '/references');
    }

    /** Remove a physician-added document. The engine refuses anything curated. */
    public function removeReference(string $sourceName): array
    {
        try {
            $response = Http::timeout($this->timeout)
                ->withHeaders($this->headers())
                ->acceptJson()
                ->delete($this->baseUrl.'/references/'.rawurlencode($sourceName));
        } catch (ConnectionException $e) {
            throw new EngineUnavailableException(
                "The clinical engine is not reachable at {$this->baseUrl} ({$e->getMessage()})."
            );
        }

        return $this->handleDocument($response, '/references');
    }

    // --- internals --------------------------------------------------------------

    /**
     * Like `handle`, but a 422 here is about the document, not about us.
     *
     * Everywhere else a 422 means this application and the engine's models have drifted —
     * our bug, hidden from the user. On these routes it means the file was too large, or
     * unreadable, or already present: things the person who chose the file needs to be
     * told, in the engine's own words. Same for the 403 that protects the curated library.
     */
    private function handleDocument($response, string $path): array
    {
        $body = $response->json() ?? [];

        if (in_array($response->status(), [403, 422], true)) {
            throw new EngineRefusedException(
                $body['detail'] ?? 'That document could not be added.',
                $body['error'] ?? 'document_rejected',
            );
        }

        return $this->handle($response, $path);
    }

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

        // 502 is the engine reporting that the model failed it, not that it is down. The
        // engine is answering — it is telling us the reply was unusable.
        if ($response->status() === 502) {
            throw new EngineModelException(
                $body['detail'] ?? 'The assistant returned a reply that could not be used.',
                $body['error'] ?? 'model_reply_invalid',
            );
        }

        throw new EngineUnavailableException(
            "Engine call to {$path} failed with HTTP {$response->status()}: ".$response->body()
        );
    }
}
