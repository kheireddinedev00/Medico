<?php

use App\Http\Controllers\AuthController;
use App\Http\Controllers\ConsultationController;
use App\Http\Controllers\Icd10Controller;
use App\Http\Controllers\PatientController;
use App\Http\Controllers\PatientIntakeController;
use App\Http\Controllers\ReportController;
use App\Http\Controllers\WaitingRoomController;
use App\Engine\EngineClient;
use Illuminate\Support\Facades\Route;

/*
|--------------------------------------------------------------------------
| API routes
|--------------------------------------------------------------------------
|
| The consultation routes below mirror the engine's state machine one-for-one. That
| repetition is deliberate: each step the physician takes is its own endpoint, so the audit
| log and the permission check both have something specific to name. A single
| /consultations/{id}/advance endpoint taking an action string would be shorter and would
| make "who ordered these tests" unanswerable.
|
| Roles are enforced at the route, not inside the controllers, so the permission model can
| be read in one place. Doctors run consultations; nurses handle the queue and vitals;
| patients read their own chart and nothing else.
|
*/

Route::post('/login', [AuthController::class, 'login']);

Route::get('/engine/health', fn () => response()->json(EngineClient::fromConfig()->health()));

Route::middleware('auth:sanctum')->group(function () {
    Route::post('/logout', [AuthController::class, 'logout']);
    Route::get('/me', [AuthController::class, 'me']);

    // The curated code list, searchable, so a physician can code a diagnosis the assistant
    // did not suggest without being able to invent a code.
    Route::get('/icd10', [Icd10Controller::class, 'index']);

    // The transition table, served by the engine so the UI never keeps its own copy.
    Route::get('/workflow', fn () => response()->json(EngineClient::fromConfig()->workflow()));

    // The triage ladder, the recommended actions and the red-flag list. Served for the
    // same reason: a priority explained by the UI's own copy of the rules is a priority
    // explained wrongly the first time the rule file changes.
    Route::get('/triage/rules', fn () => response()->json(EngineClient::fromConfig()->triageRules()))
        ->middleware('role:nurse,doctor,admin');

    // Reading the record. Patients are scoped to their own chart inside the controller.
    Route::middleware('role:doctor,nurse,admin,patient')->group(function () {
        Route::get('/patients', [PatientController::class, 'index']);
        Route::get('/patients/{patient}', [PatientController::class, 'show']);
        Route::get('/patients/{patient}/timeline', [PatientController::class, 'timeline']);
    });

    /*
     * Intake — registering a patient and taking their history.
     *
     * The nurse's routes, and the only way allergies, medications and conditions ever enter
     * the system. The assistant cannot write to the record and the consultation captures the
     * encounter rather than the background, so an allergy missed here is an allergy the
     * safety screen will never see.
     */
    Route::middleware('role:nurse,doctor,admin')->group(function () {
        Route::post('/patients', [PatientIntakeController::class, 'store']);
        Route::patch('/patients/{patient}', [PatientIntakeController::class, 'update']);

        Route::post('/patients/{patient}/allergies', [PatientIntakeController::class, 'addAllergy']);
        Route::delete('/patients/{patient}/allergies/{allergy}', [PatientIntakeController::class, 'removeAllergy']);

        Route::post('/patients/{patient}/medications', [PatientIntakeController::class, 'addMedication']);
        Route::post('/patients/{patient}/medications/{medication}/stop', [PatientIntakeController::class, 'stopMedication']);
        Route::delete('/patients/{patient}/medications/{medication}', [PatientIntakeController::class, 'removeMedication']);

        Route::post('/patients/{patient}/conditions', [PatientIntakeController::class, 'addCondition']);
        Route::delete('/patients/{patient}/conditions/{condition}', [PatientIntakeController::class, 'removeCondition']);
    });

    /*
     * The waiting room.
     *
     * The nurse runs the queue: who arrived, what they are here about, and their vitals.
     * The doctor reads it and starts consultations from it, but does not manage it — a
     * doctor adding people to the queue is how two people end up owning the same list.
     */
    Route::prefix('waiting-room')->group(function () {
        Route::get('/', [WaitingRoomController::class, 'index'])->middleware('role:nurse,doctor,admin');

        Route::middleware('role:nurse,admin')->group(function () {
            Route::post('/', [WaitingRoomController::class, 'store']);
            // Recording vitals also triages. One action for the nurse, because a screen
            // with a separate "now score them" button is a screen where half the queue
            // ends up unscored.
            Route::post('/{entry}/vitals', [WaitingRoomController::class, 'vitals']);
            Route::post('/{entry}/visit', [WaitingRoomController::class, 'setVisit']);

            // Re-score without re-entering observations: for when the engine was down at
            // the time, or the readings have gone stale. It cannot detect deterioration —
            // only new measurements can change a priority.
            Route::post('/{entry}/retriage', [WaitingRoomController::class, 'retriage']);

            Route::delete('/{entry}', [WaitingRoomController::class, 'destroy']);
        });

        /*
         * Overriding the agent.
         *
         * Open to doctors as well as nurses, unlike the rest of queue management. A doctor
         * who looks at a waiting patient and disagrees with the machine must be able to
         * say so immediately — that is the clinician-in-the-loop the whole design rests
         * on, and routing it through "ask a nurse to change it" would make the override
         * theoretical.
         */
        Route::middleware('role:nurse,doctor,admin')->group(function () {
            Route::post('/{entry}/priority', [WaitingRoomController::class, 'priority']);
            Route::delete('/{entry}/priority', [WaitingRoomController::class, 'clearPriority']);
        });

        // The doctor marks someone as seen when they start the consultation.
        Route::post('/{entry}/seen', [WaitingRoomController::class, 'seen'])
            ->middleware('role:nurse,doctor,admin');
    });

    // Open visits for one patient, so the nurse can say which one they are back about.
    Route::get('/patients/{patient}/resumable-visits', [WaitingRoomController::class, 'resumableVisits'])
        ->middleware('role:nurse,doctor,admin');

    // The triage vitals offered as a starting point when the doctor opens the patient.
    Route::get('/patients/{patient}/triage-vitals', [WaitingRoomController::class, 'forConsultation'])
        ->middleware('role:doctor');

    // Reports. Uploading transcribes; analysing is a separate, explicit act.
    Route::middleware('role:doctor,nurse,admin,patient')->group(function () {
        Route::get('/patients/{patient}/reports', [ReportController::class, 'index']);
        Route::get('/reports/{report}', [ReportController::class, 'show']);
    });
    Route::middleware('role:doctor,nurse')->group(function () {
        Route::post('/patients/{patient}/reports', [ReportController::class, 'store']);
    });
    // Interpreting a report is a clinical act, so it is the doctor's alone.
    Route::post('/reports/{report}/analyse', [ReportController::class, 'analyse'])
        ->middleware('role:doctor');

    // The consultation itself. Doctors only — every route here either asks the assistant
    // for advice or records a clinical decision.
    Route::middleware('role:doctor')->prefix('consultations')->group(function () {
        Route::post('/', [ConsultationController::class, 'store']);
        // Visits still open, so a consultation left waiting for results can be found again.
        Route::get('/open', [ConsultationController::class, 'open']);

        Route::prefix('{visit}')->group(function () {
            Route::get('/', [ConsultationController::class, 'show']);
            // Deleting a visit runs against the append-only grain of the rest of the
            // record, so the whole visit is written to the audit log before it goes.
            Route::delete('/', [ConsultationController::class, 'destroy'])
                ->middleware('role:doctor,admin');
            Route::get('/next-states', [ConsultationController::class, 'nextStates']);
            Route::get('/soap', [ConsultationController::class, 'soap']);

            // Releases the patient from the waiting room. Navigating away does not.
            Route::post('/leave', [ConsultationController::class, 'leave']);

            Route::post('/findings', [ConsultationController::class, 'findings']);

            // --- the assistant: suggestions, logged as suggestions ---
            Route::post('/assess', [ConsultationController::class, 'assess']);
            Route::post('/suggest/codes', [ConsultationController::class, 'codes']);
            Route::post('/suggest/investigations', [ConsultationController::class, 'investigations']);
            Route::post('/suggest/medications', [ConsultationController::class, 'medications']);
            // Screens drugs the physician typed. Reports; never refuses.
            Route::post('/check-medications', [ConsultationController::class, 'checkMedications']);

            // --- the physician: decisions, written to the record ---
            Route::post('/diagnosis', [ConsultationController::class, 'selectDiagnosis']);
            Route::post('/diagnosis/code', [ConsultationController::class, 'setCode']);
            Route::post('/diagnosis/revise', [ConsultationController::class, 'reviseDiagnosis']);

            Route::post('/investigate', [ConsultationController::class, 'investigate']);
            Route::post('/investigations', [ConsultationController::class, 'orderInvestigations']);
            Route::post('/investigations/skip', [ConsultationController::class, 'skipInvestigations']);
            Route::post('/investigations/more', [ConsultationController::class, 'orderMoreInvestigations']);

            Route::post('/results', [ConsultationController::class, 'recordResults']);
            Route::post('/results/from-reports', [ConsultationController::class, 'recordResultsFromReports']);

            Route::post('/treat', [ConsultationController::class, 'treat']);
            Route::post('/prescribe', [ConsultationController::class, 'prescribe']);
            Route::post('/follow-up', [ConsultationController::class, 'followUp']);
            Route::post('/complete', [ConsultationController::class, 'complete']);
            Route::post('/note', [ConsultationController::class, 'note']);
        });
    });
});
