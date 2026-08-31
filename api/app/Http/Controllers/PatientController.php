<?php

namespace App\Http\Controllers;

use App\Models\Patient;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Reading the record.
 *
 * A patient account is scoped to its own chart — `authorise()` below is the only thing
 * standing between one patient and everyone else's history, so it runs on every route that
 * takes a patient id.
 */
class PatientController extends Controller
{
    public function index(Request $request): JsonResponse
    {
        $user = $request->user();

        $query = Patient::query()->orderBy('full_name');

        // A patient sees exactly one row: their own.
        if ($user->role === 'patient') {
            $query->whereKey($user->patient_id);
        }

        if ($search = $request->string('search')->trim()->value()) {
            $query->where(function ($q) use ($search) {
                $q->where('full_name', 'like', "%{$search}%")->orWhere('id', 'like', "%{$search}%");
            });
        }

        // 100 rather than 25: the list is filtered and sorted in the browser, and a filter
        // that only searches the visible page is a filter that lies. A clinic large enough
        // to overflow this needs server-side filtering, not a bigger number.
        $patients = $query->withCount('visits')->paginate(100);

        // Computed, never stored — a stored age is wrong the day after a birthday. The
        // chart already does this; the list needs it too now that it can be filtered on.
        $patients->getCollection()->transform(function (Patient $patient) {
            $patient->age = $patient->date_of_birth?->age ?? $patient->age_years;

            return $patient;
        });

        return response()->json(['patients' => $patients]);
    }

    public function show(Request $request, string $patientId): JsonResponse
    {
        $patient = Patient::with(['allergies', 'medications', 'chronicConditions'])->findOrFail($patientId);
        $this->authorise($request, $patient);

        return response()->json([
            'patient' => $patient,
            // Computed, never stored — a stored age is wrong the day after a birthday.
            'age' => $patient->date_of_birth?->age ?? $patient->age_years,
            // The doctor is part of the visit, not a footnote: "who saw me last time" is
            // one of the first things anyone asks of a record.
            'visits' => $patient->visits()->with('doctor:id,name')
                ->orderByDesc('created_at')
                ->get([
                    'id', 'status', 'chief_complaint', 'working_diagnosis_label',
                    'working_diagnosis_icd10', 'doctor_id', 'created_at',
                ])
                ->map(fn ($v) => [
                    'id' => $v->id,
                    'status' => $v->status,
                    'chief_complaint' => $v->chief_complaint,
                    'working_diagnosis_label' => $v->working_diagnosis_label,
                    'working_diagnosis_icd10' => $v->working_diagnosis_icd10,
                    'doctor' => $v->doctor?->name,
                    'created_at' => $v->created_at,
                ]),
        ]);
    }

    /**
     * Visits and reports in date order.
     *
     * Reports are listed separately rather than folded into their visit, because a report
     * may belong to no visit at all — results arrive late, out of order, and sometimes for
     * nothing anyone ordered.
     */
    public function timeline(Request $request, string $patientId): JsonResponse
    {
        $patient = Patient::findOrFail($patientId);
        $this->authorise($request, $patient);

        $visits = $patient->visits()->with(['differentials', 'investigations', 'prescriptions'])
            ->orderByDesc('created_at')->get();

        $reports = $patient->reports()->orderByDesc('uploaded_at')
            ->get(['id', 'kind', 'report_type', 'report_date', 'visit_id', 'uploaded_at', 'analysed_at']);

        return response()->json([
            'visits' => $visits,
            'reports' => $reports->map(fn ($r) => [
                'id' => $r->id,
                'label' => $r->label(),
                'kind' => $r->kind,
                'visit_id' => $r->visit_id,
                'uploaded_at' => $r->uploaded_at,
                // Null analysis is a clinical state, not a loading placeholder: uploaded,
                // but nobody has yet asked what it means.
                'analysed' => $r->analysed_at !== null,
            ]),
        ]);
    }

    private function authorise(Request $request, Patient $patient): void
    {
        $user = $request->user();

        abort_if(
            $user->role === 'patient' && $user->patient_id !== $patient->id,
            403,
            'You may only view your own record.',
        );
    }
}
