<?php

use App\Engine\EngineContractException;
use App\Engine\EngineModelException;
use App\Engine\EngineRefusedException;
use App\Engine\EngineUnavailableException;
use App\Http\Middleware\EnsureRole;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Http\Request;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        $middleware->alias(['role' => EnsureRole::class]);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        /*
         * The three engine failures are genuinely different things, and collapsing them
         * into one 500 would hide the only one a clinician can act on.
         */

        // The workflow does not allow that step. Not an error — the engine's message is
        // written for a clinician and travels to them unchanged.
        $exceptions->render(function (EngineRefusedException $e, Request $request) {
            return response()->json([
                'message' => $e->getMessage(),
                'reason' => $e->reason,
            ], 409);
        });

        // We sent the engine something its models reject. Always our bug — the two schemas
        // have drifted — so the detail is surfaced in local environments to make it
        // findable, and withheld in production where it would leak internals.
        $exceptions->render(function (EngineContractException $e, Request $request) {
            return response()->json([
                'message' => 'The clinical engine rejected the request payload.',
                'detail' => app()->isLocal() ? $e->detail : null,
            ], 500);
        });

        /*
         * The model failed, not the service.
         *
         * Reported separately from "unavailable" because the two demand different things
         * from whoever reads them: this one usually clears on a retry, and saying the
         * assistant is down when it is plainly running teaches people to ignore the errors.
         */
        $exceptions->render(function (EngineModelException $e, Request $request) {
            return response()->json([
                'message' => 'The assistant could not read that reliably. Nothing was saved — try again.',
                'detail' => app()->isLocal() ? $e->getMessage() : null,
                'retryable' => true,
            ], 502);
        });

        // The assistant is unavailable. Said plainly, because an empty differential
        // rendered as though it were an answer is the dangerous failure here.
        $exceptions->render(function (EngineUnavailableException $e, Request $request) {
            return response()->json([
                'message' => 'The clinical assistant is unavailable. The record is unaffected.',
                'detail' => $e->getMessage(),
            ], 503);
        });
    })->create();
