<?php

/**
 * Where the clinical engine lives.
 *
 * Bind it to localhost. This service answers clinical questions about named patients and
 * has no authentication of its own beyond the shared secret — it is meant to be reachable
 * from this application and from nothing else.
 */

return [
    'url' => env('ENGINE_URL', 'http://127.0.0.1:8001'),

    // Must match SERVICE_API_KEY in the engine's .env. Sent as X-Service-Key.
    'key' => env('ENGINE_KEY', ''),

    // Generous on purpose. A free-tier model call with retrieval can take a while, and a
    // timeout that fires mid-assessment looks to the doctor like the assistant had nothing
    // to say.
    'timeout' => env('ENGINE_TIMEOUT', 120),

    /*
     * Reading a document is in a different league from every other call.
     *
     * A single scanned page takes around a minute on the free tier, and a PDF costs roughly
     * that per page — so a three-page report is three minutes of genuine work, not a hang.
     * Sharing the 120-second timeout with the other endpoints would abort exactly the
     * requests most worth waiting for, after the model had already done the work.
     *
     * The real answer is a queued job. Until then, the timeout tells the truth about how
     * long this takes.
     */
    'extract_timeout' => env('ENGINE_EXTRACT_TIMEOUT', 600),

    /*
     * Triage without the language model.
     *
     * The agent's deterministic layers produce a complete, ordered decision on their own;
     * the model only adds risk signals it can spot in the wording and may raise a priority
     * one step. Turning it off therefore degrades the explanation, not the safety.
     *
     * On in tests, where a live model call would make the queue tests slow and their
     * outcome dependent on a free-tier endpoint being awake. Worth turning on in a demo
     * too, if the room's wifi is not to be trusted.
     */
    'triage_rules_only' => env('ENGINE_TRIAGE_RULES_ONLY', false),
];
