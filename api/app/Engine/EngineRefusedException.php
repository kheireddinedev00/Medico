<?php

namespace App\Engine;

use RuntimeException;

/**
 * The engine refused on clinical grounds — an invalid transition, a diagnosis revised at
 * the wrong point, a completed visit being reopened.
 *
 * This is not an error in the ordinary sense. The request was well formed and the workflow
 * simply does not allow it, so the message is written for a clinician and should reach them
 * unchanged rather than being replaced with something generic.
 */
class EngineRefusedException extends RuntimeException
{
    public function __construct(string $message, public readonly string $reason = 'consultation_error')
    {
        parent::__construct($message);
    }
}
