<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Restricts a route to one or more roles.
 *
 *     Route::post('/consultations', ...)->middleware('role:doctor');
 *     Route::post('/waiting-room', ...)->middleware('role:nurse,doctor');
 *
 * The separation this enforces is clinical, not merely administrative. A nurse records
 * vitals and manages the queue; a doctor reaches diagnoses and prescribes. Nothing in the
 * consultation workflow should be reachable by an account that is not a clinician, and a
 * patient account may only ever read its own chart.
 *
 * A deactivated account is refused here too. `is_active` is how a clinic revokes access
 * without deleting a user whose name is attached to signed records.
 */
class EnsureRole
{
    public function handle(Request $request, Closure $next, string ...$roles): Response
    {
        $user = $request->user();

        if (! $user) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        if (! $user->is_active) {
            return response()->json(['message' => 'This account is deactivated.'], 403);
        }

        if (! in_array($user->role, $roles, true)) {
            return response()->json([
                'message' => 'Your role does not permit this action.',
                'required' => $roles,
                'role' => $user->role,
            ], 403);
        }

        return $next($request);
    }
}
