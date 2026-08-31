<?php

namespace App\Http\Controllers;

use App\Models\User;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Hash;
use Illuminate\Validation\ValidationException;

/**
 * Token authentication for the React client.
 *
 * Sanctum personal access tokens rather than session cookies: the front end is a separate
 * origin and the engine calls are server-to-server, so there is no reason to carry CSRF
 * machinery for a client that never posts a form.
 */
class AuthController extends Controller
{
    public function login(Request $request): JsonResponse
    {
        $credentials = $request->validate([
            'email' => ['required', 'email'],
            'password' => ['required', 'string'],
        ]);

        $user = User::where('email', $credentials['email'])->first();

        // One message for both failures, deliberately. Distinguishing "no such account"
        // from "wrong password" tells an attacker which clinician emails are real.
        if (! $user || ! Hash::check($credentials['password'], $user->password)) {
            throw ValidationException::withMessages([
                'email' => ['Those credentials do not match our records.'],
            ]);
        }

        if (! $user->is_active) {
            throw ValidationException::withMessages([
                'email' => ['This account is deactivated.'],
            ]);
        }

        // One token per login, so signing out on one device does not sign the clinician
        // out of the consultation they have open on another.
        $token = $user->createToken('api-'.now()->timestamp)->plainTextToken;

        return response()->json([
            'token' => $token,
            'user' => $this->profile($user),
        ]);
    }

    public function logout(Request $request): JsonResponse
    {
        $request->user()->currentAccessToken()->delete();

        return response()->json(['message' => 'Signed out.']);
    }

    public function me(Request $request): JsonResponse
    {
        return response()->json(['user' => $this->profile($request->user())]);
    }

    private function profile(User $user): array
    {
        return [
            'id' => $user->id,
            'name' => $user->name,
            'email' => $user->email,
            'role' => $user->role,
            // Present only for patient accounts, and it scopes everything they can read.
            'patient_id' => $user->patient_id,
            'avatar' => $user->avatar,
            'title' => $user->title,
        ];
    }

    /**
     * Edit your own account: the name you are shown as, your title, and your photo.
     *
     * Deliberately narrow. Role, email and active status are an administrator's to set —
     * an account that can change its own role is not an account anyone can audit. This is
     * the presentation of a person, not their permissions.
     */
    public function updateProfile(Request $request): JsonResponse
    {
        $data = $request->validate([
            'name' => ['sometimes', 'string', 'max:255'],
            'title' => ['sometimes', 'nullable', 'string', 'max:120'],
            // Downscaled to 256px in the browser before it is sent, which lands well under
            // this. The cap is here because a client is not something to trust about size.
            'avatar' => ['sometimes', 'nullable', 'string', 'max:2000000'],
        ]);

        if (array_key_exists('avatar', $data) && $data['avatar'] !== null) {
            abort_unless(
                preg_match('#^data:image/(png|jpeg|webp);base64,#', $data['avatar']) === 1,
                422,
                'A profile photo must be a PNG, JPEG or WebP image.',
            );
        }

        $user = $request->user();
        $user->update($data);

        return response()->json(['user' => $this->profile($user->fresh())]);
    }
}
