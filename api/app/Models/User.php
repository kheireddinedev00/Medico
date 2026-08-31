<?php

namespace App\Models;

// use Illuminate\Contracts\Auth\MustVerifyEmail;
use Database\Factories\UserFactory;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;
use Laravel\Sanctum\HasApiTokens;

class User extends Authenticatable
{
    /** @use HasFactory<UserFactory> */
    use HasApiTokens, HasFactory, Notifiable;

    /**
     * The attributes that are mass assignable.
     *
     * @var list<string>
     */
    protected $fillable = [
        'name',
        'email',
        'password',
        'role',
        'patient_id',
        'is_active',
    ];

    /**
     * Mirrors the column defaults.
     *
     * Without this, a freshly created User has `is_active` unset in memory — the database
     * default only applies on insert — and the role middleware reads null as "deactivated".
     * The account works on the next request and not on the one that made it, which is a
     * miserable thing to debug.
     */
    protected $attributes = [
        'role' => self::ROLE_DOCTOR,
        'is_active' => true,
    ];

    public const ROLE_DOCTOR = 'doctor';
    public const ROLE_NURSE = 'nurse';
    public const ROLE_PATIENT = 'patient';
    public const ROLE_ADMIN = 'admin';

    public function isDoctor(): bool
    {
        return $this->role === self::ROLE_DOCTOR;
    }

    public function isNurse(): bool
    {
        return $this->role === self::ROLE_NURSE;
    }

    /**
     * A patient user sees one chart: their own.
     *
     * Doctors and nurses have no patient_id, so this is null for them — which is why every
     * patient-scoped query checks the role first rather than trusting a null to mean
     * "unrestricted".
     */
    public function patient(): \Illuminate\Database\Eloquent\Relations\BelongsTo
    {
        return $this->belongsTo(Patient::class);
    }

    /**
     * Visits this clinician performed.
     *
     * Only ever populated for doctors — `doctor_id` is stamped when a consultation becomes
     * a record. Nurses record vitals and manage the queue, neither of which produces a
     * visit, so this is empty for them rather than meaningless.
     */
    public function visits(): \Illuminate\Database\Eloquent\Relations\HasMany
    {
        return $this->hasMany(Visit::class, 'doctor_id');
    }

    /**
     * The attributes that should be hidden for serialization.
     *
     * @var list<string>
     */
    protected $hidden = [
        'password',
        'remember_token',
    ];

    /**
     * Get the attributes that should be cast.
     *
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'email_verified_at' => 'datetime',
            'password' => 'hashed',
            'is_active' => 'boolean',
        ];
    }
}
