<?php

namespace App\Http\Controllers;

use App\Models\User;
use App\Support\Auditor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Hash;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Rules\Password;

/**
 * Clinic accounts, managed by the administrator.
 *
 * Doctors and nurses do not sign themselves up. Somebody who can prescribe, or who can enter
 * the allergy list a prescription is screened against, holds an account that was granted
 * deliberately — and the audit trail is only worth reading if every account belongs to a
 * known person.
 *
 * Two rules run through this class:
 *
 * **Accounts are deactivated, never deleted.** A user id appears on visits, audit rows,
 * uploaded reports and saved notes. Deleting the row would either destroy that history or
 * leave it pointing at nobody; `is_active` refuses the login while the name stays attached
 * to what they did.
 *
 * **Patient logins are not made here.** They are bound to one patient record and are a
 * different thing from staff; mixing them into this screen is how somebody ends up with a
 * clinician account by accident.
 */
class StaffController extends Controller
{
    /** Roles an administrator may grant from this screen. */
    private const STAFF_ROLES = [User::ROLE_DOCTOR, User::ROLE_NURSE, User::ROLE_ADMIN];

    public function index(Request $request): JsonResponse
    {
        $users = User::whereIn('role', self::STAFF_ROLES)
            ->orderBy('role')->orderBy('name')
            ->get(['id', 'name', 'email', 'role', 'is_active', 'created_at']);

        return response()->json([
            'staff' => $users->map(fn (User $u) => [
                'id' => $u->id,
                'name' => $u->name,
                'email' => $u->email,
                'role' => $u->role,
                'is_active' => (bool) $u->is_active,
                'created_at' => $u->created_at,
                // Shown so an administrator can see they are about to lock themselves out.
                'is_you' => $u->id === $request->user()->id,
            ]),
        ]);
    }

    public function store(Request $request): JsonResponse
    {
        $data = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'email' => ['required', 'email', 'max:255', 'unique:users,email'],
            'role' => ['required', Rule::in(self::STAFF_ROLES)],
            // Laravel's default rules rather than a bare length: this account can read every
            // patient in the clinic.
            'password' => ['required', 'string', Password::min(8)],
        ]);

        $user = User::create([
            'name' => $data['name'],
            'email' => $data['email'],
            'role' => $data['role'],
            'password' => Hash::make($data['password']),
            'is_active' => true,
        ]);

        // Never the password, obviously — not even hashed. An audit log is read by people.
        Auditor::record($request->user()->id, 'staff.created', $user, null, [
            'name' => $user->name, 'email' => $user->email, 'role' => $user->role,
        ], $request->ip());

        return response()->json(['user' => $this->present($user, $request)], 201);
    }

    public function update(Request $request, User $user): JsonResponse
    {
        $this->refuseNonStaff($user);

        $data = $request->validate([
            'name' => ['sometimes', 'string', 'max:255'],
            'email' => ['sometimes', 'email', 'max:255', Rule::unique('users', 'email')->ignore($user->id)],
            'role' => ['sometimes', Rule::in(self::STAFF_ROLES)],
            'is_active' => ['sometimes', 'boolean'],
        ]);

        // An administrator who removes their own admin role, or deactivates themselves, is
        // locked out of the only screen that could undo it.
        if ($user->id === $request->user()->id) {
            abort_if(
                ($data['role'] ?? $user->role) !== User::ROLE_ADMIN,
                422,
                'You cannot remove your own administrator role — ask another administrator.',
            );
            abort_if(
                array_key_exists('is_active', $data) && ! $data['is_active'],
                422,
                'You cannot deactivate your own account.',
            );
        }

        $before = $user->only(array_keys($data));
        $user->update($data);

        Auditor::record($request->user()->id, 'staff.updated', $user, $before, $data, $request->ip());

        return response()->json(['user' => $this->present($user->fresh(), $request)]);
    }

    /**
     * Set a new password for someone who has lost theirs.
     *
     * Every token they hold is revoked at the same time. A password reset that leaves the
     * old sessions alive does not actually take the account back.
     */
    public function resetPassword(Request $request, User $user): JsonResponse
    {
        $this->refuseNonStaff($user);

        $data = $request->validate([
            'password' => ['required', 'string', Password::min(8)],
        ]);

        $user->update(['password' => Hash::make($data['password'])]);
        $user->tokens()->delete();

        Auditor::record($request->user()->id, 'staff.password_reset', $user, null, null, $request->ip());

        return response()->json([
            'message' => 'Password changed. Any sessions that account had open are now signed out.',
        ]);
    }

    /**
     * Deactivate. Deliberately not a delete.
     *
     * The account's name is attached to visits, prescriptions, uploaded reports and audit
     * rows. Removing the row would either take that history with it or leave it pointing at
     * nobody, and "who saw this patient" is exactly the question a record exists to answer.
     */
    public function deactivate(Request $request, User $user): JsonResponse
    {
        $this->refuseNonStaff($user);

        abort_if(
            $user->id === $request->user()->id,
            422,
            'You cannot deactivate your own account.',
        );

        $user->update(['is_active' => false]);
        $user->tokens()->delete();

        Auditor::record($request->user()->id, 'staff.deactivated', $user, null, null, $request->ip());

        return response()->json([
            'user' => $this->present($user->fresh(), $request),
            'message' => 'Account deactivated. Their name stays on everything they recorded.',
        ]);
    }

    /** Doctors available to take patients, for the nurse's assignment control. */
    public function doctors(Request $request): JsonResponse
    {
        return response()->json([
            'doctors' => User::where('role', User::ROLE_DOCTOR)
                ->where('is_active', true)
                ->orderBy('name')
                ->get(['id', 'name']),
        ]);
    }

    private function present(User $user, Request $request): array
    {
        return [
            'id' => $user->id,
            'name' => $user->name,
            'email' => $user->email,
            'role' => $user->role,
            'is_active' => (bool) $user->is_active,
            'created_at' => $user->created_at,
            'is_you' => $user->id === $request->user()->id,
        ];
    }

    /** Patient logins belong to the patient record, not to this screen. */
    private function refuseNonStaff(User $user): void
    {
        abort_if(
            ! in_array($user->role, self::STAFF_ROLES, true),
            404,
            'That account is not a staff account.',
        );
    }
}
